use crate::tasks::Tasks;
use anyhow::{Context, Result};
use serde_json::{json, Value};
use std::{
    fs::File,
    io::{BufRead, BufReader},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{atomic::{AtomicBool, Ordering}, Arc, Condvar, Mutex},
    time::Duration,
};
use voiceink_core::{engine::Engine, storage};

/// One network-only helper per cycle, independent of the AI/local-ASR worker.
pub struct ServerSync {
    engine: Arc<Engine>,
    tasks: Arc<Tasks>,
    directory: PathBuf,
    state: Mutex<Value>,
    child: Mutex<Option<Child>>,
    wake: (Mutex<bool>, Condvar),
    stop: AtomicBool,
}
impl ServerSync {
    pub fn open(engine: Arc<Engine>, tasks: Arc<Tasks>, directory: PathBuf) -> Arc<Self> {
        Arc::new(Self {
            engine, tasks, directory,
            state: Mutex::new(json!({"status":"checking","message":"正在检查服务器连接"})),
            child: Mutex::new(None), wake: (Mutex::new(false), Condvar::new()),
            stop: AtomicBool::new(false),
        })
    }
    pub fn state(&self) -> Value { self.state.lock().unwrap().clone() }
    fn set(&self, state: Value) { *self.state.lock().unwrap() = state; }
    pub fn now(&self) {
        *self.wake.0.lock().unwrap() = true;
        self.wake.1.notify_one();
    }
    pub fn settings_changed(&self) {
        if let Some(child) = self.child.lock().unwrap().as_mut() { let _ = child.kill(); }
        self.now();
    }
    pub fn shutdown(&self) {
        self.stop.store(true, Ordering::SeqCst);
        if let Some(child) = self.child.lock().unwrap().as_mut() { let _ = child.kill(); }
        self.now();
    }
    pub fn start(self: &Arc<Self>) {
        let sync = self.clone();
        std::thread::spawn(move || {
            while !sync.stop.load(Ordering::SeqCst) {
                if let Err(error) = sync.cycle() {
                    if !sync.stop.load(Ordering::SeqCst) {
                        sync.set(json!({"status":"error","message":"服务器同步未完成，稍后自动重试","error":error.to_string()}));
                    }
                }
                let _ = storage::save(&sync.engine.data.join("server-sync-state.json"), &sync.state());
                let requested = sync.wake.0.lock().unwrap();
                let (mut requested, _) = sync.wake.1.wait_timeout_while(requested, Duration::from_secs(30), |ready| !*ready && !sync.stop.load(Ordering::SeqCst)).unwrap();
                *requested = false;
            }
        });
    }
    fn cycle(&self) -> Result<()> {
        let settings = self.tasks.settings();
        if !settings.server_enabled {
            self.set(json!({"status":"disabled","message":"服务器同步已关闭"}));
            return Ok(());
        }
        if settings.server_url.is_empty() || settings.server_token.is_empty() {
            self.set(json!({"status":"error","message":"请在设置中补充服务器地址和访问令牌"}));
            return Ok(());
        }
        self.set(json!({"status":"checking","message":"正在检查服务器录音"}));
        let config = self.engine.data.join("server-sync-settings.json");
        storage::save(&config, &json!({"server_url":settings.server_url,"server_token":settings.server_token,"server_ca_cert":settings.server_ca_cert}))?;
        let mut command = Command::new(&settings.python_path);
        command.arg("-X").arg("utf8")
            .arg(self.directory.join("helpers/server_sync.py"))
            .arg("--data").arg(&self.engine.data).arg("--settings").arg(config)
            .env("PYTHONDONTWRITEBYTECODE", "1").env("PYTHONIOENCODING", "utf-8")
            .stdin(Stdio::null()).stdout(Stdio::piped())
            .stderr(File::create(self.engine.data.join("server-sync.log"))?);
        #[cfg(windows)] {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let stdout = {
            let mut slot = self.child.lock().unwrap();
            if self.stop.load(Ordering::SeqCst) { return Ok(()); }
            let mut child = command.spawn().context("无法启动服务器同步，请检查设置中的 Python 路径")?;
            let stdout = child.stdout.take().context("无法读取服务器同步进度")?;
            *slot = Some(child);
            stdout
        };
        let mut errors = Vec::new();
        for line in BufReader::new(stdout).lines() {
            let Ok(line) = line else { break };
            let Ok(value) = serde_json::from_str::<Value>(&line) else { continue };
            let event = &value["watchrec_sync"];
            match event["type"].as_str() {
                Some("status") => self.set(event.clone()),
                Some("ready") if !self.stop.load(Ordering::SeqCst) => {
                    let path = event["path"].as_str().context("同步缺少音频路径")?;
                    let server = event["server"].as_str().context("同步缺少服务器地址")?;
                    if let Err(error) = self.engine.add_server_file(&PathBuf::from(path), server, &event["item"], &settings.default_asr) {
                        errors.push(format!("{}：{error}", event["item"]["id"].as_str().unwrap_or("录音")));
                    } else {
                        self.tasks.refresh_imports();
                    }
                }
                _ => {}
            }
        }
        let status = self.child.lock().unwrap().take().map(|mut c| c.wait()).transpose()?;
        if !errors.is_empty() {
            let mut state = self.state();
            state["status"] = json!("error");
            state["error"] = json!(errors.join("；"));
            state["message"] = json!("音频已保存，加入转写队列失败，稍后重试");
            self.set(state);
        } else if status.is_some_and(|s| !s.success()) && self.state()["status"] != "error" {
            self.set(json!({"status":"error","message":"服务器同步已中断，稍后重试","error":"下载断点已保存"}));
        }
        Ok(())
    }
}
