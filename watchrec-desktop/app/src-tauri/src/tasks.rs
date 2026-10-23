use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use std::{
    collections::{HashMap, HashSet},
    fs::{self, File},
    io::{BufRead, BufReader},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use voiceink_core::{engine::Engine, storage, transcription::Job};

const AI_CONCURRENCY: usize = 3;

#[derive(Default)]
struct SlotState {
    ids: HashSet<String>,
    closed: bool,
}
#[derive(Default)]
struct TaskSlots(Mutex<SlotState>);
struct TaskLease {
    slots: Arc<TaskSlots>,
    id: String,
}
impl TaskSlots {
    fn claim(self: &Arc<Self>, id: &str, limit: usize) -> Option<TaskLease> {
        let mut state = self.0.lock().unwrap();
        if state.closed || state.ids.len() >= limit || !state.ids.insert(id.into()) {
            return None;
        }
        Some(TaskLease { slots: self.clone(), id: id.into() })
    }
    fn active(&self) -> Vec<String> {
        let mut ids: Vec<_> = self.0.lock().unwrap().ids.iter().cloned().collect();
        ids.sort();
        ids
    }
    fn contains(&self, id: &str) -> bool {
        self.0.lock().unwrap().ids.contains(id)
    }
    fn close(&self) { self.0.lock().unwrap().closed = true; }
}
impl Drop for TaskLease {
    fn drop(&mut self) { self.slots.0.lock().unwrap().ids.remove(&self.id); }
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    pub ai_enabled: bool,
    pub llm_base_url: String,
    pub llm_api_key: String,
    pub llm_model: String,
    pub python_path: String,
    pub default_asr: String,
    pub server_enabled: bool,
    pub server_url: String,
    pub server_token: String,
    pub server_ca_cert: String,
}
impl Default for Settings {
    fn default() -> Self {
        #[cfg(windows)]
        let python = PathBuf::from("python.exe");
        #[cfg(not(windows))]
        let python = PathBuf::from("python3");
        Self {
            ai_enabled: false,
            llm_base_url: String::new(),
            llm_api_key: String::new(),
            llm_model: String::new(),
            python_path: python.to_string_lossy().into(),
            default_asr: "local".into(),
            server_enabled: false,
            server_url: String::new(),
            server_token: String::new(),
            server_ca_cert: String::new(),
        }
    }
}
impl Settings { fn ai_enabled(&self) -> bool { self.ai_enabled && !self.llm_base_url.trim().is_empty() && !self.llm_model.trim().is_empty() } }
#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct AiState {
    pub status: String,
    pub phase: String,
    pub done: u32,
    pub total: u32,
    pub full_text: String,
    pub summary: String,
    pub headline: String,
    pub source_preserved_parts: Vec<usize>,
    pub pipeline: String,
    pub draft_text: String,
    pub error: Option<String>,
    pub error_kind: String,
    pub retry_after: u64,
    pub retry_count: u32,
}

fn unix_now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs()
}

fn retry_due(state: &AiState, now: u64) -> bool {
    state.status != "retry-wait" || state.retry_after <= now
}

fn defer_network_retry(state: &mut AiState, now: u64) {
    const DELAYS: [u64; 5] = [30, 60, 120, 300, 600];
    let delay = DELAYS[(state.retry_count as usize).min(DELAYS.len() - 1)];
    state.retry_count = state.retry_count.saturating_add(1);
    state.retry_after = now.saturating_add(delay);
    state.status = "retry-wait".into();
    state.phase = "AI 连接暂时中断，进度已保存，等待自动重试".into();
}

// The Python checkpoint also owns signature and parts. Status updates must
// preserve those fields so a retry or shutdown can resume completed passages.
fn persist_state(path: &Path, state: &AiState) -> Result<()> {
    let mut checkpoint: serde_json::Value = if path.exists() {
        storage::load(path)?
    } else {
        serde_json::json!({})
    };
    let fields = serde_json::to_value(state)?;
    checkpoint.as_object_mut().context("整理进度格式无效")?
        .extend(fields.as_object().unwrap().clone());
    storage::save(path, &checkpoint)
}

fn apply_event(old: AiState, event: &serde_json::Value) -> Result<AiState> {
    let mut merged = serde_json::to_value(old)?;
    merged.as_object_mut().unwrap().extend(event.as_object().context("整理事件格式无效")?.clone());
    Ok(serde_json::from_value(merged)?)
}
pub struct Tasks {
    engine: Arc<Engine>,
    directory: PathBuf,
    settings: Mutex<Settings>,
    states: Mutex<HashMap<String, AiState>>,
    children: Mutex<HashMap<String, Child>>,
    slots: Arc<TaskSlots>,
    stop: AtomicBool,
}
impl Tasks {
    pub fn open(engine: Arc<Engine>, directory: PathBuf) -> Arc<Self> {
        let settings: Settings = fs::read_to_string(engine.data.join("settings.json"))
            .ok()
            .and_then(|s| serde_json::from_str(s.trim_start_matches('\u{feff}')).ok())
            .unwrap_or_default();
        let states = engine
            .all_jobs()
            .iter()
            .map(|job| {
                let mut state = storage::load::<AiState>(&job.directory.join("enrichment.json")).unwrap_or_default();
                if state.status == "running" {
                    state.status = "queued".into();
                    state.phase = "进度已保存，等待继续整理".into();
                }
                (job.info().id, state)
            })
            .collect();
        Arc::new(Self {
            engine,
            directory,
            settings: Mutex::new(settings),
            states: Mutex::new(states),
            children: Mutex::new(HashMap::new()),
            slots: Arc::new(TaskSlots::default()),
            stop: AtomicBool::new(false),
        })
    }
    pub fn settings(&self) -> Settings {
        self.settings.lock().unwrap().clone()
    }
    pub fn public_settings(&self) -> serde_json::Value {
        let s = self.settings();
        serde_json::json!({"ai_enabled":s.ai_enabled, "llm_base_url":s.llm_base_url,"llm_model":s.llm_model,
            "api_key_set":!s.llm_api_key.is_empty(),"python_path":s.python_path,"default_asr":s.default_asr,
            "server_enabled":s.server_enabled,"server_url":s.server_url,
            "server_token_set":!s.server_token.is_empty(),"server_ca_cert":s.server_ca_cert})
    }
    pub fn save_settings(&self, mut settings: Settings) -> Result<()> {
        if !matches!(settings.default_asr.as_str(), "local") {
            bail!("未知转写模式")
        }
        let mut current = self.settings.lock().unwrap();
        if settings.llm_api_key.trim().is_empty() {
            settings.llm_api_key = current.llm_api_key.clone();
        }
        if settings.server_token.trim().is_empty() {
            settings.server_token = current.server_token.clone();
        }
        settings.server_url = settings.server_url.trim().trim_end_matches('/').into();
        storage::save(&self.engine.data.join("settings.json"), &settings)?;
        *current = settings;
        Ok(())
    }
    pub fn state(&self, id: &str) -> Option<AiState> {
        self.states.lock().unwrap().get(id).cloned()
    }
    pub fn view_states(&self, selected: Option<&str>) -> HashMap<String, AiState> {
        self.states
            .lock()
            .unwrap()
            .iter()
            .map(|(id, state)| {
                let visible = selected == Some(id.as_str());
                (
                    id.clone(),
                    AiState {
                        status: state.status.clone(),
                        phase: state.phase.clone(),
                        done: state.done,
                        total: state.total,
                        headline: state.headline.clone(),
                        source_preserved_parts: state.source_preserved_parts.clone(),
                        pipeline: state.pipeline.clone(),
                        draft_text: if visible { state.draft_text.clone() } else { String::new() },
                        error: state.error.clone(),
                        error_kind: state.error_kind.clone(),
                        retry_after: state.retry_after,
                        retry_count: state.retry_count,
                        full_text: if visible {
                            state.full_text.clone()
                        } else {
                            String::new()
                        },
                        summary: if visible {
                            state.summary.clone()
                        } else {
                            String::new()
                        },
                    },
                )
            })
            .collect()
    }
    pub fn refresh_imports(&self) {
        let mut states = self.states.lock().unwrap();
        for job in self.engine.all_jobs() {
            states.entry(job.info().id).or_insert_with(|| {
                storage::load(&job.directory.join("enrichment.json")).unwrap_or_default()
            });
        }
    }
    pub fn active(&self) -> Vec<String> { self.slots.active() }
    fn set(&self, id: &str, state: AiState) {
        self.states.lock().unwrap().insert(id.into(), state);
    }
    pub fn busy(&self, id: &str) -> bool {
        self.slots.contains(id)
    }
    pub fn retry(&self, id: &str) -> Result<()> {
        let job = self.engine.job(id)?;
        if !job.finished() {
            bail!("请等原文识别完成后再整理")
        }
        if job.checkpoint().raw.trim().is_empty() {
            bail!("这条录音未识别到文字，无法生成全文与总结")
        }
        let _lease = self.slots.claim(id, usize::MAX).context("这条录音正在处理中")?;
        if !self.settings().ai_enabled() {
            bail!("请先在设置中启用 AI 并填写接口和模型")
        }
        let mut state = self.state(id).unwrap_or_default();
        state.status = "queued".into();
        state.phase = "等待整理".into();
        state.error = None;
        state.error_kind.clear();
        state.retry_after = 0;
        state.retry_count = 0;
        persist_state(&job.directory.join("enrichment.json"), &state)?;
        self.set(id, state);
        Ok(())
    }
    pub fn use_local(&self, id: &str) -> Result<()> {
        let _lease = self.slots.claim(id, usize::MAX).context("这条录音正在处理中，请等当前处理完成")?;
        let job = self.engine.job(id)?;
        if job.archive.as_ref().is_some_and(|a| !a.closed()) {
            bail!("请先停止录音，再使用本地模型")
        }
        let previous = self.state(id).unwrap_or_default();
        let state = AiState {
            status: "waiting-asr".into(),
            phase: "等待本地原文".into(),
            ..Default::default()
        };
        self.set(id, state.clone());
        if let Err(error) = self.engine.use_local(id) {
            self.set(id, previous);
            return Err(error);
        }
        let path = job.directory.join("enrichment.json");
        if path.exists() {
            fs::copy(&path, job.directory.join("上次整理.json"))?;
        }
        storage::save(&path, &state)?;
        self.set(id, state);
        Ok(())
    }
    pub fn start(self: &Arc<Self>) {
        let this = self.clone();
        std::thread::spawn(move || {
            let mut last_job: Option<String> = None;
            while !this.stop.load(Ordering::SeqCst) && !this.engine.stopping() {
                let mut did_work = false;
                let mut jobs = this.engine.all_jobs();
                if let Some(index) = jobs.iter().position(|job| Some(job.info().id) == last_job) {
                    let next = (index + 1) % jobs.len();
                    jobs.rotate_left(next);
                }
                for job in jobs {
                    if this.stop.load(Ordering::SeqCst) || this.engine.stopping() {
                        break;
                    }
                    let info = job.info();
                    if info.provider == "local"
                        && !job.finished()
                        && !job.paused()
                        && !this.engine.recording_active()
                        && !this.engine.asr_active(&info.id)
                        && job.archive.as_ref().is_none_or(|a| a.closed())
                    {
                        let Some(_lease) = this.slots.claim(&info.id, AI_CONCURRENCY) else { continue };
                        job.local_status("正在加载本地模型");
                        let result = this.run_helper("local", &job).and_then(|_| {
                            let v: serde_json::Value =
                                storage::load(&job.directory.join("local-result.json"))?;
                            job.finish_local(
                                v["raw"].as_str().context("本地识别没有返回原文")?.into(),
                                v["duration_seconds"].as_f64().unwrap_or(0.0),
                            )
                        });
                        if let Err(error) = result {
                            job.local_status(&format!("本地转写未完成：{error}"));
                            if !this.stop.load(Ordering::SeqCst) { let _ = job.set_paused(true); }
                        } else {
                            let state = AiState::default();
                            let _ = storage::save(&job.directory.join("enrichment.json"), &state);
                            this.set(&info.id, state);
                        }
                        did_work = true;
                    }
                    if !job.finished()
                        || this.engine.asr_active(&info.id)
                    {
                        continue;
                    }
                    let state = this.state(&info.id).unwrap_or_else(|| {
                        storage::load(&job.directory.join("enrichment.json")).unwrap_or_default()
                    });
                    if !retry_due(&state, unix_now()) { continue; }
                    if matches!(
                        state.status.as_str(),
                        "done" | "error" | "imported" | "paused" | "waiting-asr"
                    ) {
                        continue;
                    }
                    if job.checkpoint().raw.trim().is_empty() {
                        if state.status != "empty" {
                            let state = AiState {
                                status: "empty".into(),
                                phase: "未识别到文字，无法生成全文与总结".into(),
                                ..Default::default()
                            };
                            let _ = storage::save(&job.directory.join("enrichment.json"), &state);
                            this.set(&info.id, state);
                        }
                        continue;
                    }
                    if !this.settings().ai_enabled() {
                        this.set(
                            &info.id,
                            AiState {
                                status: "unconfigured".into(),
                                phase: "设置 AI 后即可生成全文与总结".into(),
                                ..state
                            },
                        );
                        continue;
                    }
                    let Some(lease) = this.slots.claim(&info.id, AI_CONCURRENCY) else { continue };
                    // State may have changed between inspection and reservation.
                    let latest = this.state(&info.id).unwrap_or_default();
                    if !job.finished() || this.engine.asr_active(&info.id) || !retry_due(&latest, unix_now()) || matches!(
                        latest.status.as_str(), "done" | "error" | "imported" | "paused" | "waiting-asr"
                    ) { continue; }
                    did_work = true;
                    last_job = Some(info.id);
                    let worker = this.clone();
                    std::thread::spawn(move || {
                        let _lease = lease; // Reserved through checkpoint persistence.
                        worker.enrich_passage(&job);
                    });
                }
                if !did_work {
                    std::thread::sleep(Duration::from_millis(400));
                }
            }
        });
    }
    fn enrich_passage(&self, job: &Arc<Job>) {
        if let Err(error) = self.run_helper("enrich", job) {
            let id = job.info().id;
            let mut state = self.state(&id).unwrap_or_default();
            if self.stop.load(Ordering::SeqCst) || self.engine.stopping() {
                state.status = "queued".into();
                state.phase = "进度已保存，等待继续整理".into();
                state.error = None;
            } else {
                state.status = "error".into();
                state.error = Some(error.to_string());
                if state.error_kind == "network" {
                    defer_network_retry(&mut state, unix_now());
                }
            }
            let _ = persist_state(&job.directory.join("enrichment.json"), &state);
            self.set(&id, state);
        }
    }
    fn run_helper(&self, mode: &str, job: &Arc<Job>) -> Result<()> {
        if self.stop.load(Ordering::SeqCst) || self.engine.stopping() {
            bail!("已停止，进度已保存")
        }
        let settings = self.settings();
        let id = job.info().id;
        let settings_path = job.directory.join("helper-settings.json");
        storage::save(&settings_path, &settings)?;
        let mut command = Command::new(&settings.python_path);
        command
            .arg(self.directory.join("helpers/tasks.py"))
            .arg(mode)
            .arg("--directory")
            .arg(&job.directory)
            .arg("--settings")
            .arg(&settings_path)
            .env("PYTHONDONTWRITEBYTECODE", "1").env("PYTHONIOENCODING", "utf-8")
            .env("PYTHONUNBUFFERED", "1")
            .stdout(Stdio::piped())
            .stderr(Stdio::from(File::create(job.directory.join("helper.log"))?));
        if mode == "enrich" {
            command.arg("--max-parts").arg("1");
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let mut child = match command.spawn() {
            Ok(child) => child,
            Err(error) => {
                let _ = fs::remove_file(&settings_path);
                return Err(error).context("无法启动处理程序，请在设置中检查 Python 路径");
            }
        };
        let stdout = child.stdout.take().unwrap();
        {
            let mut active = self.children.lock().unwrap();
            if self.stop.load(Ordering::SeqCst) || self.engine.stopping() {
                let _ = child.kill();
            }
            active.insert(id.clone(), child);
        }
        let mut last_error = None;
        for line in BufReader::new(stdout).lines() {
            let Ok(line) = line else { break };
            let Ok(value) = serde_json::from_str::<serde_json::Value>(&line) else {
                continue;
            };
            let Some(event) = value.get("watchrec") else {
                continue;
            };
            if let Some(error) = event["error"].as_str() {
                last_error = Some(error.to_owned());
            }
            if mode == "local" {
                if let Some(phase) = event["phase"].as_str() {
                    job.local_status(phase);
                }
            } else if let Ok(state) = apply_event(self.state(&id).unwrap_or_default(), event) {
                self.set(&id, state);
            }
        }
        let mut child = self.children.lock().unwrap().remove(&id).context("处理已取消")?;
        let status = child.wait()?;
        let _ = fs::remove_file(settings_path);
        if !status.success() {
            let error = last_error.unwrap_or_else(|| "处理未完成，进度已保存，可重试".into());
            bail!(error)
        }
        Ok(())
    }
    pub fn shutdown(&self) {
        self.slots.close();
        self.stop.store(true, Ordering::SeqCst);
        for child in self.children.lock().unwrap().values_mut() {
            let _ = child.kill();
        }
    }
    pub fn wait_for_shutdown(&self, limit: Duration) {
        let until = Instant::now() + limit;
        while !self.active().is_empty() && Instant::now() < until {
            std::thread::sleep(Duration::from_millis(20));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn network_retry_waits_without_losing_progress_and_survives_restart() {
        let mut state = AiState { status: "error".into(), error_kind: "network".into(),
            done: 9, total: 13, draft_text: "已完成段落".into(), ..Default::default() };
        let mut now = 1000;
        for delay in [30, 60, 120, 300, 600, 600] {
            defer_network_retry(&mut state, now);
            assert_eq!(state.retry_after, now + delay);
            assert!(!retry_due(&state, now + delay - 1));
            assert!(retry_due(&state, now + delay));
            state = serde_json::from_str(&serde_json::to_string(&state).unwrap()).unwrap();
            assert_eq!((state.done, state.total), (9, 13));
            assert_eq!(state.draft_text, "已完成段落");
            now += delay;
        }
        state.status = "queued".into();
        assert!(retry_due(&state, 0)); // Manual retry does not wait on an old deadline.
    }

    #[test]
    fn partial_events_preserve_progress_and_allow_explicit_reset() {
        let state = AiState { status: "running".into(), done: 1, total: 4,
            draft_text: "已保存正文".into(), ..Default::default() };
        let state = apply_event(state, &serde_json::json!({"phase":"自动重试 1/3"})).unwrap();
        assert_eq!(state.status, "running");
        let state = apply_event(state, &serde_json::json!({"status":"error", "error":"中断"})).unwrap();
        assert_eq!((state.done, state.total), (1, 4));
        assert_eq!(state.draft_text, "已保存正文");
        let state = apply_event(state, &serde_json::json!({"draft_text":"", "error":null})).unwrap();
        assert!(state.draft_text.is_empty());
        assert!(state.error.is_none());
    }

    #[test]
    fn concurrent_claims_enforce_capacity_and_exclusive_recordings() {
        for same_recording in [false, true] {
            let slots = Arc::new(TaskSlots::default());
            let ready = Arc::new(std::sync::Barrier::new(9));
            let release = Arc::new(std::sync::Barrier::new(9));
            let handles: Vec<_> = (0..8).map(|index| {
                let slots = slots.clone();
                let ready = ready.clone();
                let release = release.clone();
                std::thread::spawn(move || {
                    let id = if same_recording { "same".into() } else { index.to_string() };
                    let lease = slots.claim(&id, AI_CONCURRENCY);
                    ready.wait();
                    release.wait();
                    drop(lease);
                })
            }).collect();
            ready.wait();
            let active_count = slots.active().len();
            release.wait();
            for handle in handles { handle.join().unwrap(); }
            assert_eq!(active_count, if same_recording { 1 } else { AI_CONCURRENCY });
            assert!(slots.active().is_empty());
            let lease = slots.claim("next", AI_CONCURRENCY).unwrap();
            assert!(slots.contains("next"));
            slots.close();
            assert!(slots.claim("after-close", AI_CONCURRENCY).is_none());
            drop(lease);
            assert!(slots.active().is_empty());
        }
    }

    #[test]
    fn status_updates_preserve_python_resume_checkpoint() {
        let directory = std::env::temp_dir()
            .join(format!("status-checkpoint-test-{}", std::process::id()));
        fs::create_dir_all(&directory).unwrap();
        let path = directory.join("enrichment.json");
        storage::save(&path, &serde_json::json!({
            "signature": "input-and-prompt-hash", "parts": ["已完成段落", null],
            "status": "running", "draft_text": "已完成段落", "done": 1, "total": 2,
            "error": null
        })).unwrap();
        for (status, error) in [("error", Some("网络中断".to_owned())), ("queued", None)] {
            let mut state: AiState = storage::load(&path).unwrap();
            state.status = status.into();
            state.error = error;
            persist_state(&path, &state).unwrap();
            let saved: serde_json::Value = storage::load(&path).unwrap();
            assert_eq!(saved["signature"], "input-and-prompt-hash");
            assert_eq!(saved["parts"], serde_json::json!(["已完成段落", null]));
            assert_eq!(saved["done"], 1);
            assert_eq!(saved["status"], status);
        }
        fs::remove_dir_all(directory).unwrap();
    }
}
