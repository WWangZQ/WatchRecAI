use crate::{archive::Archive, audio::AudioFile, storage};
use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};
use std::{path::{Path, PathBuf},sync::{Arc, Mutex}};
#[derive(Clone, Serialize, Deserialize)]
pub struct RemoteSource {
    pub server: String,
    pub id: String,
}
#[derive(Clone, Serialize, Deserialize)]
pub struct JobInfo {
    pub version: u32,
    pub id: String,
    pub title: String,
    #[serde(default)]
    pub custom_title: Option<String>,
    pub created_utc: String,
    pub source_file: String,
    pub microphone: bool,
    pub paused: bool,
    pub duration_seconds: Option<f64>,
    #[serde(default)]
    pub imported_from: Option<String>,
    #[serde(default)]
    pub remote: Option<RemoteSource>,
    #[serde(default = "default_provider")]
    pub provider: String,
}
fn default_provider() -> String {
    "local".into()
}

#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct Checkpoint { pub raw: String, pub finished: bool, pub acknowledged_pcm_bytes: u64, pub published_pcm_bytes: u64, pub error: Option<String> }
#[derive(Clone, Serialize)]
pub struct Progress {
    pub id: String,
    pub title: String,
    pub created_utc: String,
    pub microphone: bool,
    pub paused: bool,
    pub state: String,
    pub status: String,
    pub finished: bool,
    pub captured_bytes: u64,
    pub acknowledged_bytes: u64,
    pub duration_seconds: Option<f64>,
    pub raw: String,
    pub capture_error: Option<String>,
    pub directory: PathBuf,
    pub provider: String,
}
pub struct Job {
    pub directory: PathBuf,
    pub archive: Option<Arc<Archive>>,
    info: Mutex<JobInfo>,
    checkpoint: Mutex<Checkpoint>,
    state: Mutex<(String, String)>,
}
impl Job {
    fn new(
        directory: PathBuf,
        info: JobInfo,
        archive: Option<Arc<Archive>>,
        checkpoint: Checkpoint,
    ) -> Result<Self> {
        // Server audio is validated inside our download cache by Engine before admission.
        if !((info.imported_from.is_some() || info.remote.is_some())
            && !info.microphone
            && Path::new(&info.source_file).is_absolute())
        {
            storage::local_name(&info.source_file)?;
        }
        ensure!(info.version == 2, "不支持的转写记录版本");
        let finished = checkpoint.finished;
        Ok(Self {
            directory,
            archive,
            info: Mutex::new(info),
            checkpoint: Mutex::new(checkpoint),
            state: Mutex::new(if finished {
                ("complete".into(), "转写完成 · 已自动保存".into())
            } else {
                ("pending".into(), "等待转写 · 音频已保存".into())
            }),
        })
    }
    pub fn create_microphone(archive: Arc<Archive>, _polish: bool) -> Result<Arc<Self>> {
        let info = Self::info_new(
            "麦克风录音.wav".into(),
            true,
            format!("麦克风 · {}", chrono::Local::now().format("%m-%d %H:%M")),
        );
        let job = Arc::new(Self::new(
            archive.directory.clone(),
            info,
            Some(archive),
            Checkpoint::default(),
        )?);
        job.save_info()?;
        job.save_checkpoint()?;
        Ok(job)
    }
    pub fn create_file(source: &Path, _polish: bool, title: String) -> Result<Arc<Self>> {
        let mut info = Self::info_new(
            source
                .file_name()
                .context("Invalid source")?
                .to_string_lossy()
                .into_owned(),
            false,
            title,
        );
        info.duration_seconds = AudioFile::open(source)?.duration;
        let job = Arc::new(Self::new(
            source.parent().context("Invalid source")?.to_owned(),
            info,
            None,
            Checkpoint::default(),
        )?);
        job.save_info()?;
        job.save_checkpoint()?;
        Ok(job)
    }
    pub fn create_server(
        directory: &Path,
        source: &Path,
        remote: RemoteSource,
        item: &serde_json::Value,
        provider: &str,
    ) -> Result<Arc<Self>> {
        ensure!(provider == "local", "未知转写模式");
        let audio = AudioFile::open(source)?;
        let mut info = Self::info_new(
            source.to_string_lossy().into(), false,
            source.file_name().context("无效录音路径")?.to_string_lossy().into(),
        );
        info.created_utc = item["recorded_at"].as_str().unwrap_or(&info.created_utc).into();
        info.duration_seconds = audio.duration.or_else(|| item["duration_sec"].as_f64());
        info.remote = Some(remote);
        info.provider = provider.into();
        // Downloaded audio joins the same local queue as imported audio.
        info.paused = false;
        let job = Arc::new(Self::new(directory.to_owned(), info, None, Checkpoint::default())?);
        job.save_info()?;
        job.save_checkpoint()?;
        Ok(job)
    }
    fn info_new(source_file: String, microphone: bool, title: String) -> JobInfo {
        JobInfo {
            version: 2,
            id: uuid::Uuid::new_v4().to_string(),
            created_utc: chrono::Utc::now().to_rfc3339(),
            source_file,
            microphone,
            title,
            custom_title: None,
            paused: false,
            duration_seconds: None,
            imported_from: None,
            remote: None,
            provider: default_provider(),
        }
    }
    pub fn recover(directory: &Path) -> Result<Arc<Self>> {
        let info: JobInfo = storage::load(&directory.join("job.json"))?;
        let archive = if info.microphone {
            Some(Arc::new(Archive::recover(directory)?))
        } else {
            None
        };
        let checkpoint: Checkpoint = storage::load(&directory.join("transcription.json"))?;
        Ok(Arc::new(Self::new(
            directory.to_owned(),
            info,
            archive,
            checkpoint,
        )?))
    }
    pub fn info(&self) -> JobInfo {
        self.info.lock().unwrap().clone()
    }
    pub fn set_provider(&self, provider: &str) -> Result<()> {
        ensure!(provider == "local", "未知转写模式");
        self.info.lock().unwrap().provider = provider.into();
        self.save_info()
    }
    pub fn local_pending(&self) -> Result<()> {
        if let Some(archive) = &self.archive {
            ensure!(archive.closed(), "请先停止录音，再使用本地模型");
        }
        self.set_provider("local")?;
        self.checkpoint.lock().unwrap().finished = false;
        self.set_paused(false)?;
        self.status("pending", "等待本地转写 · 模型按需加载");
        self.save_checkpoint()
    }
    pub fn local_status(&self, message: &str) {
        self.status("local", message);
    }
    pub fn finish_local(&self, raw: String, duration: f64) -> Result<()> {
        let previous = self.checkpoint().raw;
        if !previous.is_empty() {
            storage::write(
                &self.directory.join("上次识别原文.txt"),
                previous.as_bytes(),
            )?;
        }
        self.info.lock().unwrap().duration_seconds = Some(duration);
        {
            let mut c = self.checkpoint.lock().unwrap();
            c.raw = raw;
            c.finished = true;
            c.acknowledged_pcm_bytes = (duration * 32000.0) as u64;
            c.published_pcm_bytes = c.acknowledged_pcm_bytes;
            c.error = None;
        }
        self.status("complete", "本地转写完成");
        self.save_info()?;
        self.save_checkpoint()?;
        self.save_text()
    }
    pub fn rename(&self, title: String) -> Result<()> {
        let title = title.trim();
        self.info.lock().unwrap().custom_title = if title.is_empty() {
            None
        } else {
            Some(title.into())
        };
        self.save_info()
    }
    pub fn import_watchrec(
        directory: &Path,
        audio: &Path,
        original: &Path,
        data: &serde_json::Value,
    ) -> Result<Arc<Self>> {
        let mut info = Self::info_new(
            audio.file_name().context("无效音频路径")?.to_string_lossy().into(),
            false,
            data["title"]
                .as_str()
                .filter(|s| !s.is_empty())
                .unwrap_or_else(|| data["recorded_at"].as_str().unwrap_or("历史录音"))
                .into(),
        );
        info.created_utc = data["recorded_at"].as_str().unwrap_or("").into();
        info.duration_seconds = data["duration_sec"].as_f64();
        info.imported_from = Some(original.to_string_lossy().into());
        info.custom_title = data["title"]
            .as_str()
            .filter(|s| !s.trim().is_empty())
            .map(str::to_owned);
        info.provider = "local".into();
        let mut c = Checkpoint::default();
        c.raw = data["transcript"].as_str().unwrap_or("").into();
        c.finished = true;
        c.acknowledged_pcm_bytes = (info.duration_seconds.unwrap_or(0.0) * 32000.0) as u64;
        let job = Arc::new(Self::new(directory.to_owned(), info, None, c)?);
        job.save_info()?;
        job.save_checkpoint()?;
        job.save_text()?;
        Ok(job)
    }
    pub fn create_fresh_watchrec(
        source: &Path, original: &Path, data: &serde_json::Value,
    ) -> Result<Arc<Self>> {
        let title = original.file_stem().context("无效录音路径")?.to_string_lossy().into_owned();
        let job = Self::create_file(source, true, title.clone())?;
        {
            let mut info = job.info.lock().unwrap();
            info.imported_from = Some(original.to_string_lossy().into_owned());
            if let Some(date) = data["recorded_at"].as_str().filter(|s| !s.is_empty()) {
                info.created_utc = date.into();
            } else if let Some(date) = title.get(..19).and_then(|s| chrono::NaiveDateTime::parse_from_str(s, "%Y-%m-%d_%H-%M-%S").ok()) {
                info.created_utc = format!("{}+08:00", date.format("%Y-%m-%dT%H:%M:%S"));
            }
        }
        job.save_info()?;
        if data.is_object() {
            storage::save(&job.directory.join("旧版记录.json"), data)?;
        }
        Ok(job)
    }
    pub fn checkpoint(&self) -> Checkpoint {
        self.checkpoint.lock().unwrap().clone()
    }
    pub fn finished(&self) -> bool {
        self.checkpoint.lock().unwrap().finished
    }
    pub fn paused(&self) -> bool {
        self.info.lock().unwrap().paused
    }
    pub fn set_paused(&self, paused: bool) -> Result<()> {
        self.info.lock().unwrap().paused = paused;
        self.save_info()
    }
    pub fn progress(&self) -> Progress {
        let info = self.info();
        let c = self.checkpoint();
        let (state, status) = self.state.lock().unwrap().clone();
        let capture_error = self.archive.as_ref().and_then(|a| a.info().error);
        Progress {
            id: info.id,
            title: info.title,
            created_utc: info.created_utc,
            microphone: info.microphone,
            paused: info.paused,
            state,
            status,
            finished: c.finished,
            captured_bytes: self
                .archive
                .as_ref()
                .map_or(c.acknowledged_pcm_bytes, |a| a.captured()),
            acknowledged_bytes: c.acknowledged_pcm_bytes,
            duration_seconds: self
                .archive
                .as_ref()
                .and_then(|a| a.closed().then(|| a.captured() as f64 / 32000.0))
                .or(info.duration_seconds),
            raw: c.raw,
            capture_error,
            directory: self.directory.clone(),
            provider: info.provider,
        }
    }
    fn status(&self, state: &str, message: &str) {
        *self.state.lock().unwrap() = (state.into(), message.into());
    }
    fn save_info(&self) -> Result<()> {
        storage::save(
            &self.directory.join("job.json"),
            &*self.info.lock().unwrap(),
        )
    }
    fn save_checkpoint(&self) -> Result<()> {
        storage::save(
            &self.directory.join("transcription.json"),
            &self.checkpoint(),
        )
    }
    fn save_text(&self) -> Result<()> {
        let c = self.checkpoint();
        storage::write(&self.directory.join("原始转写.txt"), c.raw.as_bytes())
    }
}
