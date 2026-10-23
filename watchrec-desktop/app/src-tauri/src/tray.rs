use crate::{request_close, show_main};
use std::{sync::Arc, time::Duration};
use tauri::{
    image::Image,
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Manager,
};
use voiceink_core::{engine::Engine, storage};

fn icon(state: &str) -> Image<'static> {
    if state == "idle" {
        return Image::from_bytes(include_bytes!("../icons/32x32.png")).expect("bundled WatchRec icon");
    }
    let mut pixels = vec![0u8; 32 * 32 * 4];
    for y in 0..32 {
        for x in 0..32 {
            let radius = ((x as f32 - 15.5).powi(2) + (y as f32 - 15.5).powi(2)).sqrt();
            let visible = if state == "recording" {
                radius < 12.0
            } else if state == "waiting" {
                radius < 12.0 && (x < 14 || x > 17)
            } else {
                (8.0..12.0).contains(&radius)
            };
            if visible {
                let color = match state {
                    "recording" => [210, 81, 51, 255],
                    "waiting" => [186, 139, 44, 255],
                    _ => [112, 111, 106, 255],
                };
                pixels[(y * 32 + x) * 4..(y * 32 + x + 1) * 4].copy_from_slice(&color);
            }
        }
    }
    Image::new_owned(pixels, 32, 32)
}
pub fn setup(app: &tauri::App, engine: &Arc<Engine>) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "打开WatchRec", true, None::<&str>)?;
    let stop = MenuItem::with_id(app, "stop", "停止录音", false, None::<&str>)?;
    let exit = MenuItem::with_id(app, "exit", "退出WatchRec…", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(app, &[&open, &stop, &separator, &exit])?;
    let tray = TrayIconBuilder::with_id("voiceink")
        .icon(icon("idle"))
        .tooltip("WatchRec · 未录音")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_tray_icon_event(|tray, event| {
            if matches!(
                event,
                TrayIconEvent::Click {
                    button: MouseButton::Left,
                    button_state: MouseButtonState::Up,
                    ..
                } | TrayIconEvent::DoubleClick {
                    button: MouseButton::Left,
                    ..
                }
            ) {
                show_main(tray.app_handle());
            }
        })
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open" => show_main(app),
            "stop" => {
                let engine = app.state::<Arc<Engine>>().inner().clone();
                tauri::async_runtime::spawn_blocking(move || engine.stop_recording());
            }
            "exit" => request_close(app, true),
            _ => {}
        })
        .build(app)?;
    let handle = app.handle().clone();
    let engine = engine.clone();
    tauri::async_runtime::spawn(async move {
        let mut previous = String::new();
        let mut last_tooltip = String::new();
        let mut last_warning = String::new();
        loop {
            let capture = engine.capture_status();
            let active = engine.recording_active();
            let state = if capture.as_ref().is_some_and(|s| s.recording) {
                "recording"
            } else if active {
                "waiting"
            } else {
                "idle"
            };
            let tooltip = if let Some(capture) = &capture {
                if active {
                    format!(
                        "WatchRec · {} · {:02}:{:02}",
                        if capture.recording {
                            "录音中"
                        } else {
                            "麦克风等待中"
                        },
                        capture.captured_bytes / 32000 / 60,
                        capture.captured_bytes / 32000 % 60
                    )
                } else {
                    "WatchRec · 未录音".into()
                }
            } else {
                "WatchRec · 未录音".into()
            };
            if state != previous {
                let registered = tray.set_icon(Some(icon(state))).is_ok();
                if !registered {
                    show_main(&handle);
                }
                let _ = stop.set_enabled(active);
                previous = state.into();
                let _ = storage::save(
                    &engine.data.join("tray-state.json"),
                    &serde_json::json!({"state":state,"tooltip":tooltip,"registered":registered,"rect":tray.rect().ok().flatten()}),
                );
            }
            if tooltip != last_tooltip {
                let _ = tray.set_tooltip(Some(&tooltip));
                last_tooltip = tooltip;
            }
            if let Some(capture) = &capture {
                if !capture.recording
                    && capture.recovery_attempts > 0
                    && capture.message != last_warning
                {
                    last_warning = capture.message.clone();
                    show_main(&handle);
                } else if capture.recording {
                    last_warning.clear();
                }
            }
            tokio::time::sleep(Duration::from_millis(500)).await;
        }
    });
    Ok(())
}
