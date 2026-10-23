use crate::{archive::Archive, audio::MonoConverter, storage};
use anyhow::{bail, Result};
use cpal::{
    traits::{DeviceTrait, HostTrait, StreamTrait},
    FromSample, SampleFormat, SizedSample,
};
use serde::Serialize;
use std::{
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Condvar, Mutex,
    },
    time::{Duration, Instant},
};

#[derive(Clone, Serialize)]
pub struct Device {
    pub id: String,
    pub name: String,
}
pub fn devices() -> Result<Vec<Device>> {
    let _apartment = AudioApartment::enter()?;
    let host = cpal::default_host();
    let mut devices = vec![Device {
        id: String::new(),
        name: "系统默认麦克风".into(),
    }];
    for device in host.input_devices()? {
        devices.push(Device {
            id: device.id()?.to_string(),
            name: device.to_string(),
        });
    }
    Ok(devices)
}
#[derive(Clone, Serialize)]
pub struct CaptureStatus {
    pub state: String,
    pub message: String,
    pub recording: bool,
    pub peak: f64,
    pub maximum_peak: f64,
    pub captured_bytes: u64,
    pub recovery_attempts: u32,
    pub buffer_discontinuities: u32,
    pub interruptions: Vec<Interruption>,
}
#[derive(Clone, Serialize)]
pub struct Interruption {
    utc: String,
    reason: String,
    captured_bytes: u64,
}
struct Shared {
    stop: AtomicBool,
    retry: AtomicBool,
    status: Mutex<CaptureStatus>,
    last_samples: Mutex<Instant>,
    broken: AtomicBool,
    storage_failed: AtomicBool,
    done: (Mutex<bool>, Condvar),
}
pub struct Recorder {
    shared: Arc<Shared>,
    pub archive: Arc<Archive>,
}
impl Recorder {
    pub fn start(archive: Arc<Archive>, device_id: String) -> Result<Arc<Self>> {
        let shared = Arc::new(Shared {
            stop: AtomicBool::new(false),
            retry: AtomicBool::new(false),
            broken: AtomicBool::new(false),
            storage_failed: AtomicBool::new(false),
            last_samples: Mutex::new(Instant::now()),
            done: (Mutex::new(false), Condvar::new()),
            status: Mutex::new(CaptureStatus {
                state: "starting".into(),
                message: "正在启动麦克风，尚未开始录音…".into(),
                recording: false,
                peak: 0.0,
                maximum_peak: 0.0,
                captured_bytes: 0,
                recovery_attempts: 0,
                buffer_discontinuities: 0,
                interruptions: vec![],
            }),
        });
        let recorder = Arc::new(Self {
            shared: shared.clone(),
            archive: archive.clone(),
        });
        std::thread::Builder::new()
            .name("voiceink-capture".into())
            .spawn(move || supervise(shared, archive, &device_id))?;
        Ok(recorder)
    }
    pub fn status(&self) -> CaptureStatus {
        let mut status = self.shared.status.lock().unwrap().clone();
        status.captured_bytes = self.archive.captured();
        status
    }
    pub fn request_stop(&self) {
        self.shared.stop.store(true, Ordering::SeqCst);
    }
    pub fn retry(&self) {
        self.shared.retry.store(true, Ordering::SeqCst);
    }
    pub fn is_done(&self) -> bool {
        *self.shared.done.0.lock().unwrap()
    }
    pub fn wait_stopped(&self, limit: Duration) -> bool {
        self.request_stop();
        let (done, signal) = &self.shared.done;
        *signal
            .wait_timeout_while(done.lock().unwrap(), limit, |done| !*done)
            .unwrap()
            .0
    }
}
fn supervise(shared: Arc<Shared>, archive: Arc<Archive>, device_id: &str) {
    let _power = RecordingPower::new();
    let mut fatal = None;
    while !shared.stop.load(Ordering::SeqCst) {
        shared.broken.store(false, Ordering::SeqCst);
        *shared.last_samples.lock().unwrap() = Instant::now();
        let attempt = capture(&shared, &archive, device_id);
        if shared.stop.load(Ordering::SeqCst) {
            break;
        }
        let kind = attempt
            .as_ref()
            .err()
            .and_then(|e| e.downcast_ref::<cpal::Error>())
            .map(|e| e.kind());
        let requires_user = kind == Some(cpal::ErrorKind::PermissionDenied)
            || (archive.captured() == 0
                && !matches!(
                    kind,
                    Some(
                        cpal::ErrorKind::DeviceBusy
                            | cpal::ErrorKind::DeviceNotAvailable
                            | cpal::ErrorKind::HostUnavailable
                    )
                ));
        let reason = attempt
            .err()
            .map(|e| e.to_string())
            .unwrap_or_else(|| "麦克风采集已中断".into());
        {
            let mut status = shared.status.lock().unwrap();
            status.recording = false;
            status.peak = 0.0;
            status.recovery_attempts += 1;
            status.state = if shared.storage_failed.load(Ordering::SeqCst) {
                "failed"
            } else {
                "reconnecting"
            }
            .into();
            status.message = format!("录音暂时中断 · {reason} · 已有录音保留");
            status.interruptions.push(Interruption {
                utc: chrono::Utc::now().to_rfc3339(),
                reason: reason.clone(),
                captured_bytes: archive.captured(),
            });
        }
        save_capture(&shared, &archive);
        if shared.storage_failed.load(Ordering::SeqCst) {
            fatal = Some(reason);
            break;
        }
        if requires_user {
            // Do not repeatedly trigger security software authorization requests.
            // Keep the recording intent and prior audio until an explicit retry or stop.
            {
                let mut status = shared.status.lock().unwrap();
                status.state = "permission-required".into();
                status.message =
                    "麦克风尚未就绪 · 请处理系统或安全软件提示后，点击重试麦克风".into();
            }
            save_capture(&shared, &archive);
            while !shared.stop.load(Ordering::SeqCst) && !shared.retry.swap(false, Ordering::SeqCst)
            {
                std::thread::sleep(Duration::from_millis(100));
            }
        }
        for _ in 0..20 {
            if shared.stop.load(Ordering::SeqCst) {
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
    }
    if let Err(error) = archive.complete(fatal.clone()) {
        fatal = Some(error.to_string());
    }
    {
        let mut status = shared.status.lock().unwrap();
        status.recording = false;
        status.peak = 0.0;
        status.state = if fatal.is_some() { "failed" } else { "stopped" }.into();
        status.message = fatal.map_or_else(
            || "录音已停止并保存".into(),
            |e| format!("录音保存遇到问题：{e}"),
        );
    }
    save_capture(&shared, &archive);
    *shared.done.0.lock().unwrap() = true;
    shared.done.1.notify_all();
}
fn capture(shared: &Arc<Shared>, archive: &Arc<Archive>, device_id: &str) -> Result<()> {
    let _apartment = AudioApartment::enter()?;
    let host = cpal::default_host();
    let device = if device_id.is_empty() {
        host.default_input_device()
    } else {
        host.device_by_id(&device_id.parse()?)
    }
    .ok_or_else(|| {
        cpal::Error::with_message(
            cpal::ErrorKind::DeviceNotAvailable,
            "找不到所选麦克风，正在等待设备恢复",
        )
    })?;
    // CPAL 0.18's virtual default device queries ActivateAudioInterfaceAsync
    // without a timeout. On our STA worker it can wait indefinitely for the
    // marshalled completion callback. Resolve the same default endpoint ID to
    // a concrete device, which uses synchronous IMMDevice activation. This
    // does not change the Windows default; reconnect resolves it again.
    #[cfg(windows)]
    let device = host.device_by_id(&device.id()?).ok_or_else(|| {
        cpal::Error::with_message(cpal::ErrorKind::DeviceNotAvailable, "默认麦克风暂不可用")
    })?;
    {
        let mut status = shared.status.lock().unwrap();
        status.state = "starting".into();
        status.message = "正在读取麦克风格式，尚未开始录音…".into();
    }
    save_capture(shared, archive);
    let supported = device.default_input_config()?;
    let converter = Arc::new(Mutex::new(MonoConverter::new(
        supported.sample_rate(),
        supported.channels() as usize,
    )?));
    let config = supported.config();
    shared.status.lock().unwrap().message = "正在打开麦克风，如有授权提示请允许录音…".into();
    save_capture(shared, archive);
    let stream = match supported.sample_format() {
        SampleFormat::F32 => stream::<f32>(
            &device,
            config,
            converter.clone(),
            archive.clone(),
            shared.clone(),
        )?,
        SampleFormat::F64 => stream::<f64>(
            &device,
            config,
            converter.clone(),
            archive.clone(),
            shared.clone(),
        )?,
        SampleFormat::I16 => stream::<i16>(
            &device,
            config,
            converter.clone(),
            archive.clone(),
            shared.clone(),
        )?,
        SampleFormat::I32 => stream::<i32>(
            &device,
            config,
            converter.clone(),
            archive.clone(),
            shared.clone(),
        )?,
        SampleFormat::U16 => stream::<u16>(
            &device,
            config,
            converter.clone(),
            archive.clone(),
            shared.clone(),
        )?,
        other => bail!("暂不支持麦克风采样格式 {other}"),
    };
    // A native/security permission dialog may keep stream construction waiting.
    // The no-samples watchdog starts when capture can actually start, not while
    // the user is still deciding whether to grant microphone access.
    *shared.last_samples.lock().unwrap() = Instant::now();
    stream.play()?;
    let mut last_save = Instant::now();
    while !shared.stop.load(Ordering::SeqCst) && !shared.broken.load(Ordering::SeqCst) {
        let since_samples = shared.last_samples.lock().unwrap().elapsed();
        if since_samples > Duration::from_secs(5) {
            let mut status = shared.status.lock().unwrap();
            status.recording = false;
            status.state = "waiting-permission".into();
            status.message = "麦克风尚未送来音频 · 如有系统或安全软件提示，请允许录音".into();
        }
        if since_samples > Duration::from_secs(30) {
            shared.broken.store(true, Ordering::SeqCst);
            break;
        }
        if last_save.elapsed() >= Duration::from_secs(1) {
            save_capture(shared, archive);
            last_save = Instant::now();
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    drop(stream);
    let tail = converter.lock().unwrap().finish()?;
    if !tail.is_empty() {
        if let Err(error) = archive.append(&tail) {
            shared.storage_failed.store(true, Ordering::SeqCst);
            return Err(error);
        }
    }
    if shared.broken.load(Ordering::SeqCst) {
        bail!("{}", shared.status.lock().unwrap().message);
    }
    Ok(())
}
struct CaptureStream {
    stream: Option<cpal::Stream>,
    writer: Option<std::thread::JoinHandle<()>>,
}
impl CaptureStream {
    fn play(&self) -> Result<()> {
        self.stream.as_ref().unwrap().play()?;
        Ok(())
    }
}
impl Drop for CaptureStream {
    fn drop(&mut self) {
        // Dropping CPAL closes the producer before draining the remaining samples.
        self.stream.take();
        if let Some(writer) = self.writer.take() {
            let _ = writer.join();
        }
    }
}
fn stream<T>(
    device: &cpal::Device,
    config: cpal::StreamConfig,
    converter: Arc<Mutex<MonoConverter>>,
    archive: Arc<Archive>,
    shared: Arc<Shared>,
) -> Result<CaptureStream>
where
    T: SizedSample,
    f32: FromSample<T>,
{
    let (sender, receiver) = std::sync::mpsc::channel::<Vec<f32>>();
    let queued = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let queued_writer = queued.clone();
    let writer_state = shared.clone();
    // The native audio callback only copies samples into this local queue. Opus,
    // filesystem flushes, and network I/O must never hold up the audio device.
    let writer = std::thread::Builder::new()
        .name("voiceink-audio-writer".into())
        .spawn(move || {
            for samples in receiver {
                let count = samples.len();
                let peak = samples.iter().map(|v| v.abs() as f64).fold(0.0, f64::max);
                let result = converter.lock().unwrap().push(&samples).and_then(|pcm| {
                    archive.append(&pcm)?;
                    Ok(pcm.len())
                });
                queued_writer.fetch_sub(count, Ordering::SeqCst);
                match result {
                    Ok(bytes) => {
                        if bytes > 0 {
                            let mut status = writer_state.status.lock().unwrap();
                            status.captured_bytes += bytes as u64;
                            if !writer_state.broken.load(Ordering::SeqCst)
                                && !writer_state.storage_failed.load(Ordering::SeqCst)
                            {
                                status.state = "recording".into();
                                status.recording = true;
                                status.message = "正在录音 · 音频持续保存".into();
                            }
                            status.peak = peak;
                            status.maximum_peak = status.maximum_peak.max(peak);
                        }
                    }
                    Err(error) => {
                        writer_state.storage_failed.store(true, Ordering::SeqCst);
                        writer_state.broken.store(true, Ordering::SeqCst);
                        let mut status = writer_state.status.lock().unwrap();
                        status.recording = false;
                        status.message = format!("无法保存麦克风音频：{error}");
                        break;
                    }
                }
            }
        })?;
    let failure = shared.clone();
    let stream_result = device.build_input_stream(
        config,
        move |data: &[T], _| {
            if data.is_empty()
                || shared.stop.load(Ordering::SeqCst)
                || shared.storage_failed.load(Ordering::SeqCst)
            {
                return;
            }
            *shared.last_samples.lock().unwrap() = Instant::now();
            // This ceiling concerns a stalled local disk, never an offline network.
            // At 48 kHz stereo float it allows over 2 minutes of local write delay.
            if queued.load(Ordering::SeqCst).saturating_add(data.len()) > 16 * 1024 * 1024 {
                shared.storage_failed.store(true, Ordering::SeqCst);
                shared.broken.store(true, Ordering::SeqCst);
                let mut status = shared.status.lock().unwrap();
                status.recording = false;
                status.message =
                    "本地磁盘长时间无法跟上录音，正在保存已采集内容；请检查可用空间与磁盘状态"
                        .into();
                return;
            }
            let samples: Vec<f32> = data.iter().map(|v| v.to_sample::<f32>()).collect();
            queued.fetch_add(samples.len(), Ordering::SeqCst);
            if sender.send(samples).is_err() {
                shared.storage_failed.store(true, Ordering::SeqCst);
                shared.broken.store(true, Ordering::SeqCst);
                shared.status.lock().unwrap().message =
                    "本地音频保存线程已退出，已有录音保留".into();
            }
        },
        move |error| {
            if matches!(
                error.kind(),
                cpal::ErrorKind::DeviceChanged | cpal::ErrorKind::RealtimeDenied
            ) {
                return;
            }
            if error.kind() == cpal::ErrorKind::Xrun {
                // WASAPI continues supplying valid audio after this notification.
                // Restarting the stream here introduces a guaranteed multi-second gap.
                let mut status = failure.status.lock().unwrap();
                status.buffer_discontinuities += 1;
                return;
            }
            failure.status.lock().unwrap().message = format!("麦克风连接异常：{error}");
            failure.broken.store(true, Ordering::SeqCst);
        },
        Some(Duration::from_secs(15)),
    );
    match stream_result {
        Ok(stream) => Ok(CaptureStream {
            stream: Some(stream),
            writer: Some(writer),
        }),
        Err(error) => {
            let _ = writer.join();
            Err(error.into())
        }
    }
}
fn save_capture(shared: &Shared, archive: &Archive) {
    let mut status = shared.status.lock().unwrap().clone();
    status.captured_bytes = archive.captured();
    let _ = storage::save(
        &archive.directory.join("capture.json"),
        &serde_json::json!({"mechanism":"CPAL capture inside VoiceInk main process", "process_id":std::process::id(), "status":status}),
    );
}
struct RecordingPower;

// CPAL caches a process-wide IMMDeviceEnumerator. Create and use it from MTA
// workers: an STA object created on an idle thread-pool thread has no message
// pump to service later cross-thread calls. Keep the MTA alive between recordings.
pub struct AudioRuntime {
    #[cfg(windows)]
    cookie: usize,
}
impl AudioRuntime {
    pub fn new() -> Result<Self> {
        #[cfg(windows)]
        {
            let mut cookie = std::ptr::null_mut();
            let result =
                unsafe { windows_sys::Win32::System::Com::CoIncrementMTAUsage(&mut cookie) };
            anyhow::ensure!(result >= 0, "无法初始化音频 COM 运行环境：0x{result:08X}");
            Ok(Self {
                cookie: cookie as usize,
            })
        }
        #[cfg(not(windows))]
        {
            Ok(Self {})
        }
    }
}
impl Drop for AudioRuntime {
    fn drop(&mut self) {
        #[cfg(windows)]
        unsafe {
            windows_sys::Win32::System::Com::CoDecrementMTAUsage(self.cookie as _);
        }
    }
}
struct AudioApartment(std::marker::PhantomData<*mut ()>);
impl AudioApartment {
    fn enter() -> Result<Self> {
        #[cfg(windows)]
        {
            let result =
                unsafe { windows_sys::Win32::System::Com::CoInitializeEx(std::ptr::null(), 0) };
            anyhow::ensure!(result >= 0, "音频工作线程需要 MTA：0x{result:08X}");
        }
        Ok(Self(std::marker::PhantomData))
    }
}
impl Drop for AudioApartment {
    fn drop(&mut self) {
        #[cfg(windows)]
        unsafe {
            windows_sys::Win32::System::Com::CoUninitialize();
        }
    }
}
impl RecordingPower {
    fn new() -> Self {
        #[cfg(windows)]
        unsafe {
            windows_sys::Win32::System::Power::SetThreadExecutionState(0x80000001);
        }
        Self
    }
}
impl Drop for RecordingPower {
    fn drop(&mut self) {
        #[cfg(windows)]
        unsafe {
            windows_sys::Win32::System::Power::SetThreadExecutionState(0x80000000);
        }
    }
}
