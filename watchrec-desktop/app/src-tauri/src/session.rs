use std::{sync::Arc, time::Duration};
use tauri::Manager;
use voiceink_core::engine::Engine;
use windows_sys::Win32::{
    Foundation::{HWND, LPARAM, LRESULT, WPARAM},
    UI::{
        Shell::{DefSubclassProc, RemoveWindowSubclass, SetWindowSubclass},
        WindowsAndMessaging::{WM_ENDSESSION, WM_NCDESTROY, WM_QUERYENDSESSION},
    },
};
struct Context {
    app: tauri::AppHandle,
    engine: Arc<Engine>,
}
pub fn install(
    window: &tauri::WebviewWindow,
    app: tauri::AppHandle,
    engine: Arc<Engine>,
) -> anyhow::Result<()> {
    let hwnd = window.hwnd()?.0 as HWND;
    let context = Box::into_raw(Box::new(Context { app, engine }));
    if unsafe { SetWindowSubclass(hwnd, Some(callback), 0x56494E4B, context as usize) } == 0 {
        unsafe {
            drop(Box::from_raw(context));
        }
        anyhow::bail!("无法注册关机保存处理");
    }
    Ok(())
}
unsafe extern "system" fn callback(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    id: usize,
    reference: usize,
) -> LRESULT {
    let context = &*(reference as *const Context);
    match message {
        WM_QUERYENDSESSION => 1,
        WM_ENDSESSION => {
            if wparam != 0 {
                if let Some(sync) = context.app.try_state::<Arc<crate::sync::ServerSync>>() {
                    sync.shutdown();
                }
                if let Some(tasks) = context.app.try_state::<Arc<crate::tasks::Tasks>>() {
                    tasks.shutdown();
                }
                context.engine.save_for_shutdown(Duration::from_secs(3));
                context.app.exit(0);
            }
            0
        }
        WM_NCDESTROY => {
            RemoveWindowSubclass(hwnd, Some(callback), id);
            drop(Box::from_raw(reference as *mut Context));
            DefSubclassProc(hwnd, message, wparam, lparam)
        }
        _ => DefSubclassProc(hwnd, message, wparam, lparam),
    }
}
