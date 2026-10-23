use std::{path::PathBuf, sync::Arc, time::Duration};
use tauri::{Emitter, Manager};
use voiceink_core::{engine::Engine, microphone, storage};
mod tasks;
use tasks::{Settings, Tasks};
mod translations;
use translations::Translations;
mod sync;
#[cfg(target_os = "macos")]
mod media;
use sync::ServerSync;
#[cfg(windows)]
mod session;
#[cfg(desktop)]
mod tray;

#[tauri::command]
fn snapshot(
    engine: tauri::State<'_, Arc<Engine>>,
    tasks: tauri::State<'_, Arc<Tasks>>,
    sync: tauri::State<'_, Arc<ServerSync>>,
    translations: tauri::State<'_, Arc<Translations>>,
    selected: Option<String>,
) -> serde_json::Value {
    let mut value = serde_json::to_value(engine.snapshot(selected.as_deref())).unwrap();
    let ai = tasks.view_states(value["selected"]["id"].as_str());
    value["ai"] = serde_json::to_value(ai).unwrap();
    let processing = tasks.active();
    value["processing_job"] = serde_json::to_value(processing.first()).unwrap();
    value["active_asr_jobs"] = serde_json::json!(tasks.active().into_iter().filter(|id|engine.job(id).is_ok_and(|j|!j.finished())).collect::<Vec<_>>());
    value["processing_jobs"] = serde_json::to_value(processing).unwrap();
    value["server_sync"] = sync.state();
    value["translations"] = value["selected"]["id"].as_str()
        .and_then(|id| engine.job(id).ok().map(|job| {
            let full = tasks.state(id).unwrap_or_default().full_text;
            serde_json::to_value(translations.view(&job, &full)).unwrap()
        })).unwrap_or_else(|| serde_json::json!({}));
    value
}
#[tauri::command]
fn get_settings(tasks: tauri::State<'_, Arc<Tasks>>) -> serde_json::Value {
    tasks.public_settings()
}
#[tauri::command]
fn save_settings(tasks: tauri::State<'_, Arc<Tasks>>, sync: tauri::State<'_, Arc<ServerSync>>, settings: Settings) -> Result<(), String> {
    let before = tasks.settings();
    tasks.save_settings(settings).map_err(|e| e.to_string())?;
    let after = tasks.settings();
    if before.server_enabled != after.server_enabled || before.server_url != after.server_url || before.server_token != after.server_token || before.server_ca_cert != after.server_ca_cert || before.python_path != after.python_path {
        sync.settings_changed();
    }
    Ok(())
}
#[tauri::command]
fn sync_now(sync: tauri::State<'_, Arc<ServerSync>>) {
    sync.now();
}
#[tauri::command]
fn retry_ai(tasks: tauri::State<'_, Arc<Tasks>>, id: String) -> Result<(), String> {
    tasks.retry(&id).map_err(|e| e.to_string())
}
#[tauri::command]
async fn translate(
    engine: tauri::State<'_, Arc<Engine>>, tasks: tauri::State<'_, Arc<Tasks>>,
    translations: tauri::State<'_, Arc<Translations>>, id: String, target: String,
) -> Result<(), String> {
    let job = engine.job(&id).map_err(|e|e.to_string())?;
    let settings = engine.data.join("settings.json");
    let python = tasks.settings().python_path;
    let translations = translations.inner().clone();
    tauri::async_runtime::spawn_blocking(move || translations.start(job, &python, &settings, &target))
        .await.map_err(|e|e.to_string())?.map_err(|e|e.to_string())
}
#[tauri::command]
async fn use_local(tasks: tauri::State<'_, Arc<Tasks>>, id: String) -> Result<(), String> {
    let tasks = tasks.inner().clone();
    tauri::async_runtime::spawn_blocking(move || tasks.use_local(&id))
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| e.to_string())
}
#[tauri::command]
fn rename(engine: tauri::State<'_, Arc<Engine>>, id: String, title: String) -> Result<(), String> {
    engine.rename(&id, title).map_err(|e| e.to_string())
}
#[tauri::command]
async fn devices() -> Result<Vec<microphone::Device>, String> {
    tauri::async_runtime::spawn_blocking(microphone::devices)
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| e.to_string())
}
#[tauri::command]
async fn start_recording(
    engine: tauri::State<'_, Arc<Engine>>,
    device: String,
    provider: String,
) -> Result<String, String> {
    let engine = engine.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        engine.start_recording_mode(device, true, &provider)
    })
    .await
    .map_err(|e| e.to_string())?
    .map_err(|e| e.to_string())
}
#[tauri::command]
async fn stop_recording(engine: tauri::State<'_, Arc<Engine>>) -> Result<(), String> {
    let engine = engine.inner().clone();
    tauri::async_runtime::spawn_blocking(move || engine.stop_recording())
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| e.to_string())
}
#[tauri::command]
fn retry_microphone(engine: tauri::State<'_, Arc<Engine>>) {
    engine.retry_microphone();
}
#[tauri::command]
async fn add_file(
    engine: tauri::State<'_, Arc<Engine>>,
    path: String,
    provider: String,
) -> Result<String, String> {
    let engine = engine.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        engine.add_file_mode(&PathBuf::from(path), true, &provider)
    })
    .await
    .map_err(|e| e.to_string())?
    .map_err(|e| e.to_string())
}
#[tauri::command]
async fn import_watchrec(
    engine: tauri::State<'_, Arc<Engine>>,
    tasks: tauri::State<'_, Arc<Tasks>>,
    path: String,
) -> Result<usize, String> {
    let engine = engine.inner().clone();
    let tasks = tasks.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        let count = engine.import_watchrec(&PathBuf::from(path))?;
        tasks.refresh_imports();
        Ok::<_, anyhow::Error>(count)
    })
    .await
    .map_err(|e| e.to_string())?
    .map_err(|e| e.to_string())
}
#[tauri::command]
async fn import_watchrec_fresh(
    engine: tauri::State<'_, Arc<Engine>>, tasks: tauri::State<'_, Arc<Tasks>>, path: String,
) -> Result<voiceink_core::engine::ImportReport, String> {
    let engine = engine.inner().clone();
    let tasks = tasks.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        let report = engine.import_watchrec_fresh(&PathBuf::from(path))?;
        tasks.refresh_imports();
        Ok::<_, anyhow::Error>(report)
    }).await.map_err(|e|e.to_string())?.map_err(|e|e.to_string())
}
#[tauri::command]
async fn import_history(engine: tauri::State<'_, Arc<Engine>>, tasks: tauri::State<'_, Arc<Tasks>>, path: String) -> Result<voiceink_core::engine::ImportReport, String> {
    let engine = engine.inner().clone();
    let tasks = tasks.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        let result = engine.import_watchrec_fresh(&PathBuf::from(path)).map_err(|e|e.to_string());
        tasks.refresh_imports();
        result
    }).await.map_err(|e|e.to_string())?
}
#[tauri::command]
fn pause(
    engine: tauri::State<'_, Arc<Engine>>,
    tasks: tauri::State<'_, Arc<Tasks>>,
    id: String,
    paused: bool,
) -> Result<(), String> {
    if tasks.busy(&id) {
        return Err("这条录音正在处理中，请等当前处理完成".into());
    }
    engine.pause(&id, paused).map_err(|e| e.to_string())
}
#[tauri::command]
fn audio_files(
    app: tauri::AppHandle,
    engine: tauri::State<'_, Arc<Engine>>,
    id: String,
) -> Result<Vec<String>, String> {
    let job = engine.job(&id).map_err(|e| e.to_string())?;
    let files = if let Some(archive) = &job.archive {
        if !archive.closed() {
            return Ok(vec![]);
        }
        archive
            .info()
            .parts
            .iter()
            .map(|part| job.directory.join(&part.file))
            .collect::<Vec<_>>()
    } else {
        vec![job.directory.join(job.info().source_file)]
    };
    files
        .iter()
        .filter(|p| p.is_file())
        .map(|p| {
            #[cfg(target_os = "macos")]
            { Ok(app.state::<media::Media>().url(p)) }
            #[cfg(not(target_os = "macos"))]
            {
            app.asset_protocol_scope()
                .allow_file(p)
                .map_err(|e| e.to_string())?;
            Ok(p.to_string_lossy().into_owned())
            }
        })
        .collect()
}
#[tauri::command]
async fn export_text(
    engine: tauri::State<'_, Arc<Engine>>,
    tasks: tauri::State<'_, Arc<Tasks>>,
    translations: tauri::State<'_, Arc<Translations>>,
    id: String,
    field: String,
    target: Option<String>,
    path: String,
) -> Result<(), String> {
    let job = engine.job(&id).map_err(|e| e.to_string())?;
    let ai = tasks.state(&id).unwrap_or_default();
    let text = match field.as_str() {
        "raw" => job.progress().raw,
        "full_text" => ai.full_text,
        "summary" => ai.summary,
        "translation" => {
            let target = target.as_deref().unwrap_or("zh");
            let translated = translations.view(&job, &ai.full_text).remove(target).ok_or("未知译文语言")?;
            if translated.outdated { return Err("全文已经更新，请重新翻译后导出".into()) }
            translated.text
        },
        _ => return Err("未知稿件".into()),
    };
    tauri::async_runtime::spawn_blocking(move || {
        storage::write(&PathBuf::from(path), text.as_bytes())
    })
    .await
    .map_err(|e| e.to_string())?
    .map_err(|e| e.to_string())
}
#[tauri::command]
fn open_folder(
    app: tauri::AppHandle,
    engine: tauri::State<'_, Arc<Engine>>,
    id: Option<String>,
) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    let directory = if let Some(id) = id {
        engine
            .job(&id)
            .map_err(|e| e.to_string())?
            .directory
            .clone()
    } else {
        engine.data.join("sessions")
    };
    app.opener()
        .open_path(directory.to_string_lossy(), None::<&str>)
        .map_err(|e| e.to_string())
}
#[tauri::command]
fn hide_to_tray(app: tauri::AppHandle) -> Result<(), String> {
    #[cfg(desktop)]
    {
        if app.tray_by_id("voiceink").is_none() {
            return Err("托盘图标暂不可用，已保留录音窗口".into());
        }
        if let Some(window) = app.get_webview_window("main") {
            window.hide().map_err(|e| e.to_string())?;
        }
        Ok(())
    }
    #[cfg(mobile)]
    {
        let _ = app;
        Err("当前版本仅支持 Windows".into())
    }
}
#[tauri::command]
fn quit_app(app: tauri::AppHandle) {
    quit(app);
}
pub(crate) fn show_main(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}
pub(crate) fn request_close(app: &tauri::AppHandle, exit: bool) {
    if app.state::<Arc<Engine>>().recording_active() {
        show_main(app);
        let recording = app
            .state::<Arc<Engine>>()
            .capture_status()
            .is_some_and(|s| s.recording);
        let _ = app.emit(
            "close-requested",
            serde_json::json!({"exit":exit,"recording":recording}),
        );
    } else {
        quit(app.clone());
    }
}
pub(crate) fn quit(app: tauri::AppHandle) {
    app.state::<Arc<Translations>>().shutdown();
    app.state::<Arc<ServerSync>>().shutdown();
    let tasks = app.state::<Arc<Tasks>>().inner().clone();
    tasks.shutdown();
    let engine = app.state::<Arc<Engine>>().inner().clone();
    engine.request_shutdown();
    tauri::async_runtime::spawn(async move {
        let _ = tauri::async_runtime::spawn_blocking(move || {
            engine.save_for_shutdown(Duration::from_secs(3));
            tasks.wait_for_shutdown(Duration::from_secs(3));
        })
        .await;
        app.exit(0);
    });
}
fn argument(name: &str) -> Option<PathBuf> {
    let args: Vec<_> = std::env::args_os().collect();
    args.windows(2)
        .find(|a| a[0] == name)
        .map(|a| PathBuf::from(&a[1]))
}
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default();
    #[cfg(desktop)]
    let builder = builder.plugin(tauri_plugin_single_instance::init(|app, _, _| {
        show_main(app)
    }));
    let application = builder
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .invoke_handler(tauri::generate_handler![
            snapshot,
            get_settings,
            save_settings,
            sync_now,
            retry_ai,
            translate,
            use_local,
            rename,
            devices,
            start_recording,
            stop_recording,
            retry_microphone,
            add_file,
            import_watchrec,
            import_watchrec_fresh,
            import_history,
            pause,
            audio_files,
            export_text,
            open_folder,
            hide_to_tray,
            quit_app
        ])
        .setup(|app| {
            #[cfg(target_os = "macos")]
            app.manage(media::Media::start()?);
            let directory = app.path().resource_dir()?;
            let default_data = app.path().app_data_dir()?;
            let data = argument("--data-dir").unwrap_or(default_data);
            let engine = Engine::open(&data)?;
            let tasks = Tasks::open(engine.clone(), directory.clone());
            let translations = Translations::new(directory.clone());
            let sync = ServerSync::open(engine.clone(), tasks.clone(), directory);
            app.manage(engine.clone());
            app.manage(tasks.clone());
            app.manage(translations);
            app.manage(sync.clone());
            tasks.start();
            sync.start();
            let _window = tauri::WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::App("index.html".into()),
            )
            .title("WatchRec")
            .inner_size(1220.0, 820.0)
            .min_inner_size(920.0, 640.0)
            .data_directory(data.join("webview"))
            .build()?;
            #[cfg(desktop)]
            tray::setup(app, &engine)?;
            #[cfg(windows)]
            session::install(&_window, app.handle().clone(), engine.clone())?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                request_close(window.app_handle(), false);
            }
        })
        .build(tauri::generate_context!())
        .expect("WatchRec could not start");
    application.run(|app, event| {
        if let tauri::RunEvent::Exit = event {
            if let Some(sync) = app.try_state::<Arc<ServerSync>>() {
                sync.shutdown();
            }
            if let Some(tasks) = app.try_state::<Arc<Tasks>>() {
                tasks.shutdown();
            }
            if let Some(engine) = app.try_state::<Arc<Engine>>() {
                engine.save_for_shutdown(Duration::from_secs(3));
            }
            if let Some(tasks) = app.try_state::<Arc<Tasks>>() {
                tasks.wait_for_shutdown(Duration::from_secs(3));
            }
        }
    });
}
