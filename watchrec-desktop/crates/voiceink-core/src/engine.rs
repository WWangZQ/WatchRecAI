use crate::{
    archive::Archive,
    microphone::{AudioRuntime, CaptureStatus, Recorder},
    storage,
    transcription::{Job, Progress, RemoteSource},
};
use anyhow::{ensure, Context, Result};
use serde::Serialize;
use std::{
    fs::{self, File},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio_util::sync::CancellationToken;

#[derive(Serialize)]
pub struct Summary {
    pub id: String,
    pub title: String,
    pub custom_title: Option<String>,
    pub created_utc: String,
    pub finished: bool,
    pub paused: bool,
}
#[derive(Serialize)]
pub struct Snapshot {
    pub jobs: Vec<Summary>,
    pub selected: Option<Progress>,
    pub capture: Option<CaptureStatus>,
    pub recording_job: Option<String>,
    pub pending_jobs: usize,
    pub recovery_errors: Vec<String>,
    pub active_asr_jobs: Vec<String>,
}
pub struct Engine {
    _audio_runtime: AudioRuntime,
    pub data: PathBuf,
    jobs: Mutex<Vec<Arc<Job>>>,
    import_lock: Mutex<()>,
    recording: Mutex<Option<(String, Arc<Recorder>)>>,
    stop: CancellationToken,
    errors: Mutex<Vec<String>>,
    _lock: File,
}
#[derive(Default, Serialize)]
pub struct ImportReport {
    pub imported: usize,
    pub existing: usize,
    pub errors: Vec<String>,
}
impl Engine {
    pub fn open(data: &Path) -> Result<Arc<Self>> {
        fs::create_dir_all(data.join("sessions"))?;
        let lock = storage::lock(&data.join("application.lock"))?;
        let engine = Arc::new(Self {
            _audio_runtime: AudioRuntime::new()?,
            data: data.to_owned(),
            jobs: Mutex::new(vec![]),
            import_lock: Mutex::new(()),
            recording: Mutex::new(None),
            stop: CancellationToken::new(),
            errors: Mutex::new(vec![]),
            _lock: lock,
        });
        let mut directories = fs::read_dir(data.join("sessions"))?
            .filter_map(|e| e.ok())
            .map(|e| e.path())
            .filter(|p| p.is_dir())
            .collect::<Vec<_>>();
        directories.sort();
        for directory in directories {
            if !directory.join("job.json").is_file() {
                continue;
            }
            match Job::recover(&directory) {
                Ok(job) => {
                    engine.jobs.lock().unwrap().push(job);
                }
                Err(error) => engine.errors.lock().unwrap().push(format!(
                    "{}：{error}",
                    directory.file_name().unwrap().to_string_lossy()
                )),
            }
        }
        Ok(engine)
    }
    fn new_directory(&self) -> Result<PathBuf> {
        let path = self.data.join("sessions").join(format!(
            "{}_{}",
            chrono::Local::now().format("%Y-%m-%d_%H-%M-%S"),
            &uuid::Uuid::new_v4().simple().to_string()[..8]
        ));
        fs::create_dir(&path)?;
        Ok(path)
    }
    pub fn job(&self, id: &str) -> Result<Arc<Job>> {
        self.jobs
            .lock()
            .unwrap()
            .iter()
            .find(|j| j.info().id == id)
            .cloned()
            .context("找不到这条记录")
    }
    pub fn recording_active(&self) -> bool {
        self.recording
            .lock()
            .unwrap()
            .as_ref()
            .is_some_and(|(_, r)| !r.is_done())
    }
    pub fn capture_status(&self) -> Option<CaptureStatus> {
        self.recording
            .lock()
            .unwrap()
            .as_ref()
            .map(|(_, r)| r.status())
    }
    pub fn start_recording(&self, device_id: String, polish: bool) -> Result<String> {
        self.start_recording_mode(device_id, polish, "local")
    }
    pub fn start_recording_mode(
        &self,
        device_id: String,
        polish: bool,
        provider: &str,
    ) -> Result<String> {
        ensure!(provider == "local", "未知转写模式");
        ensure!(!self.stop.is_cancelled(), "程序正在退出");
        let mut capture = self.recording.lock().unwrap();
        ensure!(
            capture.as_ref().is_none_or(|(_, r)| r.is_done()),
            "已有一条录音正在进行"
        );
        let archive = Arc::new(Archive::create(&self.new_directory()?)?);
        let job = Job::create_microphone(archive.clone(), polish)?;
        job.set_provider(provider)?;
        let recorder = Recorder::start(archive, device_id)?;
        let id = job.info().id;
        *capture = Some((id.clone(), recorder));
        self.jobs.lock().unwrap().push(job);
        Ok(id)
    }
    pub fn stop_recording(&self) -> Result<()> {
        let recording = self.recording.lock().unwrap().clone();
        if let Some((_, recorder)) = recording {
            recorder.request_stop();
            ensure!(
                recorder.wait_stopped(Duration::from_secs(5)),
                "仍在等待麦克风停止，已请求结束并保留录音"
            );
        }
        Ok(())
    }
    pub fn retry_microphone(&self) {
        if let Some((_, recorder)) = self.recording.lock().unwrap().as_ref() {
            recorder.retry();
        }
    }
    pub fn add_file(&self, original: &Path, polish: bool) -> Result<String> {
        self.add_file_mode(original, polish, "local")
    }
    pub fn add_file_mode(&self, original: &Path, polish: bool, provider: &str) -> Result<String> {
        ensure!(provider == "local", "未知转写模式");
        ensure!(!self.stop.is_cancelled(), "程序正在退出");
        crate::audio::AudioFile::open(original)?;
        let directory = self.new_directory()?;
        let extension = original
            .extension()
            .and_then(|v| v.to_str())
            .unwrap_or("audio");
        let source = directory.join(format!("音频副本.{extension}"));
        copy_audio(original, &source)?;
        let job = Job::create_file(
            &source,
            polish,
            original
                .file_name()
                .context("Invalid audio path")?
                .to_string_lossy()
                .into_owned(),
        )?;
        job.set_provider(provider)?;
        let id = job.info().id;
        self.jobs.lock().unwrap().push(job);
        Ok(id)
    }
    pub fn add_server_file(
        &self, original: &Path, server: &str, item: &serde_json::Value, provider: &str,
    ) -> Result<String> {
        ensure!(!self.stop.is_cancelled(), "程序正在退出");
        let file_id = item["id"].as_str().context("服务器录音缺少标识")?;
        let server = server.trim_end_matches('/');
        let mut jobs = self.jobs.lock().unwrap();
        if let Some(job) = jobs.iter().find(|j| j.info().remote.as_ref().is_some_and(|r| r.server == server && r.id == file_id)) {
            return Ok(job.info().id);
        }
        let source = original.canonicalize().context("服务器录音尚未下载完成")?;
        let root = self.data.join("server-downloads").canonicalize()?;
        ensure!(source.starts_with(&root) && source.is_file(), "服务器录音必须位于本机下载目录");
        ensure!(source.extension().is_none_or(|e| e != "part"), "录音尚未下载完整");
        if let Some(size) = item["size_bytes"].as_u64() {
            ensure!(size > 0 && source.metadata()?.len() == size, "下载大小与服务器记录不一致");
        }
        let directory = self.new_directory()?;
        let job = Job::create_server(&directory, &source, RemoteSource { server: server.into(), id: file_id.into() }, item, provider)?;
        let id = job.info().id;
        jobs.push(job);
        Ok(id)
    }
    pub fn pause(&self, id: &str, paused: bool) -> Result<()> {
        let job = self.job(id)?;
        job.set_paused(paused)?;
        Ok(())
    }
    pub fn all_jobs(&self) -> Vec<Arc<Job>> {
        self.jobs.lock().unwrap().clone()
    }
    pub fn stopping(&self) -> bool {
        self.stop.is_cancelled()
    }
    pub fn asr_active(&self, _id: &str) -> bool { false }
    pub fn use_local(&self, id: &str) -> Result<()> { self.job(id)?.local_pending() }
    pub fn rename(&self, id: &str, title: String) -> Result<()> {
        self.job(id)?.rename(title)
    }
    pub fn import_watchrec(&self, root: &Path) -> Result<usize> {
        let mut count = 0;
        let mut pending = vec![root.to_owned()];
        while let Some(directory) = pending.pop() {
            for entry in fs::read_dir(&directory)? {
                let path = entry?.path();
                if path.is_dir() {
                    pending.push(path);
                    continue;
                }
                if path.extension().and_then(|e| e.to_str()) != Some("json") {
                    continue;
                }
                let data: serde_json::Value = match storage::load(&path) {
                    Ok(data) => data,
                    Err(_) => continue,
                };
                if data["transcript"].as_str().is_none() || data["audio_file"].as_str().is_none() {
                    continue;
                }
                let original = fs::canonicalize(&path)?;
                if self
                    .all_jobs()
                    .iter()
                    .any(|j| j.info().imported_from.as_deref() == original.to_str())
                {
                    continue;
                }
                let audio = path
                    .parent()
                    .unwrap()
                    .join(data["audio_file"].as_str().unwrap());
                if !audio.is_file() {
                    continue;
                }
                crate::audio::AudioFile::open(&audio)?;
                let destination = self.new_directory()?;
                let copied = destination.join(audio.file_name().context("无效音频路径")?);
                copy_audio(&audio, &copied)?;
                let job = Job::import_watchrec(
                    &destination,
                    &copied,
                    &original,
                    &data,
                )?;
                storage::save(
                    &destination.join("enrichment.json"),
                    &serde_json::json!({
                        "status":"imported", "phase":"历史稿件", "full_text":data["full_text"].as_str().unwrap_or(""),
                        "summary":data["summary"].as_str().unwrap_or(""), "headline":data["headline"].as_str().unwrap_or("")
                    }),
                )?;
                self.jobs.lock().unwrap().push(job);
                count += 1;
            }
        }
        Ok(count)
    }
    /// Copy every recording and enqueue a fresh local transcription. Old text
    /// is retained for reference only; it never enters the new ASR checkpoint.
    pub fn import_watchrec_fresh(&self, root: &Path) -> Result<ImportReport> {
        let _import = self.import_lock.lock().unwrap();
        let root = root.canonicalize()?;
        ensure!(root.is_dir(), "请选择旧版录音文件夹");
        let mut pending = vec![root.clone()];
        let mut sources = Vec::new();
        while let Some(directory) = pending.pop() {
            for entry in fs::read_dir(directory)? {
                let entry = entry?;
                let kind = entry.file_type()?;
                if kind.is_symlink() { continue; }
                let path = entry.path();
                if kind.is_dir() { pending.push(path); continue; }
                let extension = path.extension().and_then(|e| e.to_str()).unwrap_or("").to_lowercase();
                if kind.is_file() && matches!(extension.as_str(), "m4a"|"mp3"|"wav"|"aac"|"flac"|"ogg"|"mp4"|"webm") {
                    sources.push(path);
                }
            }
        }
        sources.sort();
        let mut report = ImportReport::default();
        for source in sources {
            ensure!(!self.stopping(), "程序正在退出，已导入的记录会保留");
            let original = source.canonicalize()?;
            ensure!(original.starts_with(&root), "录音路径超出所选文件夹");
            if self.all_jobs().iter().any(|j| j.info().imported_from.as_deref() == original.to_str()) {
                report.existing += 1;
                continue;
            }
            let result = (|| -> Result<()> {
                // Validate before allocating a new copy, so retrying a damaged
                // source does not create another orphan audio file.
                crate::audio::AudioFile::open(&original)?;
                let directory = self.new_directory()?;
                let copied = directory.join(format!("音频副本.{}", source.extension().unwrap().to_string_lossy()));
                copy_audio(&original, &copied)?;
                let previous: serde_json::Value = storage::load(&source.with_extension("json")).unwrap_or_default();
                let job = if previous["transcript"].as_str().is_some_and(|s| !s.trim().is_empty()) {
                    let job = Job::import_watchrec(&directory, &copied, &original, &previous)?;
                    storage::save(&directory.join("enrichment.json"), &serde_json::json!({
                        "status":"imported", "phase":"历史稿件", "full_text":previous["full_text"].as_str().unwrap_or(""),
                        "summary":previous["summary"].as_str().unwrap_or(""), "headline":previous["headline"].as_str().unwrap_or("")
                    }))?;
                    storage::save(&directory.join("旧版记录.json"), &previous)?;
                    job
                } else { Job::create_fresh_watchrec(&copied, &original, &previous)? };
                self.jobs.lock().unwrap().push(job);
                Ok(())
            })();
            match result {
                Ok(()) => report.imported += 1,
                Err(error) => report.errors.push(format!("{}：{error}", source.display())),
            }
        }
        Ok(report)
    }
    pub fn snapshot(&self, selected: Option<&str>) -> Snapshot {
        let recording = self.recording.lock().unwrap().clone();
        let jobs = self.jobs.lock().unwrap().clone();
        let chosen = selected
            .and_then(|id| jobs.iter().find(|j| j.info().id == id))
            .or_else(|| jobs.last());
        let pending = jobs.iter().filter(|j| !j.finished()).count();
        Snapshot {
            jobs: jobs
                .iter()
                .rev()
                .map(|j| {
                    let i = j.info();
                    Summary {
                        id: i.id,
                        title: i.title,
                        custom_title: i.custom_title,
                        created_utc: i.created_utc,
                        finished: j.finished(),
                        paused: i.paused,
                    }
                })
                .collect(),
            selected: chosen.map(|j| j.progress()),
            capture: recording.as_ref().map(|(_, r)| r.status()),
            recording_job: recording.map(|(id, _)| id),
            pending_jobs: pending,
            recovery_errors: self.errors.lock().unwrap().clone(),
            active_asr_jobs: vec![],
        }
    }
    pub fn request_shutdown(&self) {
        if let Some((_, recorder)) = self.recording.lock().unwrap().as_ref() {
            recorder.request_stop();
        }
        self.stop.cancel();
    }
    pub fn save_for_shutdown(&self, limit: Duration) {
        self.request_shutdown();
        let timer = std::time::Instant::now();
        let recording = self.recording.lock().unwrap().clone();
        let capture_stopped = recording
            .as_ref()
            .is_none_or(|(_, r)| r.wait_stopped(limit));
        if let Some((_, recorder)) = &recording {
            let _ = recorder.archive.flush();
        }
        let _ = storage::save(
            &self.data.join("shutdown.json"),
            &serde_json::json!({"utc":chrono::Utc::now(),"capture_stopped":capture_stopped,
            "queue_stopped":true,"elapsed_seconds":timer.elapsed().as_secs_f64(),
            "captured_bytes":recording.as_ref().map(|(_, r)| r.archive.captured()),"pending_jobs":self.jobs.lock().unwrap().iter().filter(|j| !j.finished()).count()}),
        );
    }
}

fn copy_audio(original: &Path, destination: &Path) -> Result<()> {
    let mut source = File::open(original)?;
    let mut target = std::fs::OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(destination)?;
    let mut buffer = [0u8; 65536];
    loop {
        let count = source.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        target.write_all(&buffer[..count])?;
    }
    target.sync_all()?;
    Ok(())
}
