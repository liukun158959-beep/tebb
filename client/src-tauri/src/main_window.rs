//! The resident workbench owns one window; closing it preserves its webview.
use tauri::{AppHandle, Emitter, Manager, Runtime, WebviewWindow};

pub fn retain_on_close<R: Runtime>(window: &WebviewWindow<R>) {
    let main = window.clone();
    window.on_window_event(move |event| {
        if let tauri::WindowEvent::CloseRequested { api, .. } = event {
            api.prevent_close();
            if let Err(error) = main.hide() {
                eprintln!("收起工作台失败：{error}");
            }
        }
    });
}

pub fn show<R: Runtime>(app: &AppHandle<R>) -> Result<(), String> {
    let window = app
        .get_webview_window("main")
        .ok_or("找不到主窗口。恢复：重启客户端。")?;
    window.unminimize().map_err(|error| error.to_string())?;
    window.show().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())?;
    Ok(())
}

#[tauri::command]
pub fn show_main(app: AppHandle) -> Result<(), String> {
    show(&app)
}

#[tauri::command]
pub fn open_today(app: AppHandle) -> Result<(), String> {
    let window = app
        .get_webview_window("main")
        .ok_or("找不到主窗口。恢复：重启客户端。")?;
    window
        .emit("open-today", true)
        .map_err(|error| error.to_string())
}
