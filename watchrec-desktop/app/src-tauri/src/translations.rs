use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{collections::HashMap, fs::File, io::{BufRead, BufReader}, path::PathBuf,
    process::{Child, Command, Stdio}, sync::{atomic::{AtomicBool, Ordering}, Arc, Mutex}};
use voiceink_core::{storage, transcription::Job};

#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct Translation {
    pub target: String,
    pub provider: String,
    pub source_hash: String,
    pub status: String,
    pub phase: String,
    pub done: u32,
    pub total: u32,
    pub text: String,
    pub error: Option<String>,
    pub outdated: bool,
}

pub struct Translations {
    directory: PathBuf,
    states: Mutex<HashMap<(String, String), Translation>>,
    child: Mutex<Option<(String, String, Child)>>,
    stopping: AtomicBool,
}

impl Translations {
    pub fn new(directory: PathBuf) -> Arc<Self> {
        Arc::new(Self { directory, states: Mutex::new(HashMap::new()), child: Mutex::new(None), stopping: AtomicBool::new(false) })
    }
    pub fn view(&self, job: &Arc<Job>, full_text: &str) -> HashMap<String, Translation> {
        let id = job.info().id;
        let hash = format!("{:x}", Sha256::digest(full_text.trim().as_bytes()));
        let mut states = self.states.lock().unwrap();
        ["zh", "en"].into_iter().map(|target| {
            let state = states.entry((id.clone(), target.into())).or_insert_with(|| {
                let mut saved: Translation = storage::load(&job.directory.join("translations").join(format!("{target}.json"))).unwrap_or_default();
                saved.target = target.into();
                if saved.status == "running" {
                    saved.status = "paused".into();
                    saved.phase = "翻译待继续".into();
                }
                saved
            });
            let mut visible = state.clone();
            visible.outdated = !visible.source_hash.is_empty() && visible.source_hash != hash;
            (target.into(), visible)
        }).collect()
    }
    pub fn start(self: &Arc<Self>, job: Arc<Job>, python: &str, settings: &std::path::Path, target: &str) -> Result<()> {
        if !matches!(target, "zh" | "en") { bail!("请选择中文或 English") }
        if self.stopping.load(Ordering::SeqCst) { bail!("程序正在退出") }
        let source: serde_json::Value = storage::load(&job.directory.join("enrichment.json")).context("请先生成全文，再翻译")?;
        let full = source["full_text"].as_str().unwrap_or("");
        if full.trim().is_empty() { bail!("请先生成全文，再翻译") }
        let mut active = self.child.lock().unwrap();
        if active.is_some() { bail!("已有一份译文正在生成，请稍后再试") }
        let id = job.info().id;
        let prior = self.view(&job, full).remove(target).unwrap_or_default();
        if prior.status == "done" && !prior.outdated { return Ok(()) }
        let mut command = Command::new(python);
        command.arg(self.directory.join("helpers/tasks.py")).arg("translate")
            .arg("--directory").arg(&job.directory).arg("--settings").arg(settings)
            .arg("--target").arg(target).env("PYTHONDONTWRITEBYTECODE", "1").env("PYTHONIOENCODING", "utf-8").env("PYTHONUNBUFFERED", "1")
            .stdout(Stdio::piped()).stderr(Stdio::from(File::create(job.directory.join("translation.log"))?));
        #[cfg(windows)] { use std::os::windows::process::CommandExt; command.creation_flags(0x08000000); }
        let mut child = command.spawn().context("无法启动翻译程序，请在设置中检查 Python 路径")?;
        let stdout = child.stdout.take().unwrap();
        let target = target.to_owned();
        *active = Some((id.clone(), target.clone(), child));
        self.states.lock().unwrap().insert((id.clone(), target.clone()), Translation {status:"running".into(), phase:"正在连接已配置的模型".into(), error:None, ..prior});
        drop(active);
        let this = self.clone();
        std::thread::spawn(move || {
            let key = (id, target);
            for line in BufReader::new(stdout).lines() {
                let Ok(line) = line else { break };
                let Ok(value) = serde_json::from_str::<serde_json::Value>(&line) else { continue };
                let Some(event) = value.get("watchrec") else { continue };
                let mut states = this.states.lock().unwrap();
                if event.get("text").is_some() {
                    if let Ok(state) = serde_json::from_value(event.clone()) { states.insert(key.clone(), state); }
                } else if let Some(error) = event["error"].as_str() {
                    let state = states.entry(key.clone()).or_default();
                    state.status = "error".into(); state.error = Some(error.into());
                }
            }
            let mut active = this.child.lock().unwrap();
            let success = active.take().and_then(|(_, _, mut child)| child.wait().ok()).is_some_and(|s|s.success());
            let mut states = this.states.lock().unwrap();
            let state = states.entry(key).or_default();
            if !success || state.status == "running" {
                state.status = "error".into();
                state.phase = "翻译待重试".into();
                state.error.get_or_insert_with(|| "翻译已中断，已完成的段落已保存，可继续翻译".into());
            }
        });
        Ok(())
    }
    pub fn shutdown(&self) {
        self.stopping.store(true, Ordering::SeqCst);
        if let Some((_, _, child)) = self.child.lock().unwrap().as_mut() { let _ = child.kill(); }
    }
}
