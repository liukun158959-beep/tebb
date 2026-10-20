// 宠物窗 Rust 壳：托管 Python 后端 + 全局鼠标轮询做点击穿透。
// 穿透关键：ignore_cursor_events 会让整窗收不到 mousemove，前端无法自判，
// 必须由 Rust 后台线程轮询全局鼠标坐标，落在角色/面板区域外才穿透。
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use tauri::{Emitter, Manager, PhysicalPosition};
mod video_login;
mod pet_chat;
use pet_chat::{set_pet_chat_visible, toggle_pet_chat, get_pet_chat_state, set_pet_chat_pinned};
mod side_browser;
use side_browser::{open_side_browser, side_browser_layout, side_browser_action};
use video_login::{open_video_login, capture_video_login, clear_video_login_window};

#[derive(Clone, serde::Serialize)]
struct BackendInfo {
    port: u16,
    token: String,
}

#[derive(Clone, serde::Deserialize)]
struct Rect {
    x: f64,
    y: f64,
    w: f64,
    h: f64,
}

struct BackendProcess(Mutex<Option<Child>>);
struct BackendFailure(Mutex<Option<String>>);
struct HitRegions(std::sync::Arc<Mutex<Vec<Rect>>>);
struct DragLock(std::sync::Arc<AtomicBool>);
struct MenuRegion(std::sync::Arc<Mutex<Option<Rect>>>);

#[tauri::command]
fn set_pet_menu_region(app: tauri::AppHandle, menu: tauri::State<MenuRegion>, rect: Option<Rect>) -> Result<(), String> {
    let should_focus = {
        let mut current = menu.0.lock().map_err(|err| err.to_string())?;
        let should_focus = current.is_none() && rect.is_some();
        *current = rect;
        should_focus
    };
    if should_focus {
        if let Some(window) = app.get_webview_window("pet") { window.set_focus().map_err(|err| err.to_string())?; }
    }
    Ok(())
}

#[tauri::command]
fn set_hit_regions(regions: tauri::State<HitRegions>, rects: Vec<Rect>) {
    if let Ok(mut guard) = regions.0.lock() {
        *guard = rects;
    }
}

#[tauri::command]
async fn backend_info(app: tauri::AppHandle, info: tauri::State<'_, BackendInfo>, failure: tauri::State<'_, BackendFailure>) -> Result<BackendInfo, String> {
    if let Some(error) = failure.0.lock().map_err(|err| err.to_string())?.clone() {
        return Err(error);
    }
    let backend = info.inner().clone();
    let port = backend.port;
    // 窗口先打开并显示启动页，健康检查在后台等待，不阻塞 UI 线程。
    tauri::async_runtime::spawn_blocking(move || wait_healthy(port, &app.state::<BackendProcess>().0))
        .await
        .map_err(|err| format!("助手启动检查失败：{err}。请查看启动终端。"))??;
    Ok(backend)
}

// 隐藏只是把宠物窗收起，主窗还在，可再唤出。不要关整个进程。
#[tauri::command]
fn set_pet_visible(app: tauri::AppHandle, visible: bool) -> Result<(), String> {
    let window = app
        .get_webview_window("pet")
        .ok_or("找不到桌宠窗口。恢复：重启客户端。")?;
    if visible {
        window.show().map_err(|err| err.to_string())?;
        window.set_focus().map_err(|err| err.to_string())?;
    } else {
        window.hide().map_err(|err| err.to_string())?;
    }
    Ok(())
}

// 拖动中禁止穿透，否则窗口一动，松手事件会丢。
#[tauri::command]
fn set_drag_lock(lock: tauri::State<DragLock>, locked: bool) {
    lock.0.store(locked, Ordering::Relaxed);
}

#[tauri::command]
fn nudge_pet(app: tauri::AppHandle, dx: f64, dy: f64) -> Result<(), String> {
    let window = app
        .get_webview_window("pet")
        .ok_or("找不到桌宠窗口。恢复：重启客户端。")?;
    let scale = window.scale_factor().map_err(|err| err.to_string())?;
    let pos = window.outer_position().map_err(|err| err.to_string())?;
    let nx = pos.x + (dx * scale).round() as i32;
    let ny = pos.y + (dy * scale).round() as i32;
    window
        .set_position(tauri::PhysicalPosition::new(nx, ny))
        .map_err(|err| err.to_string())
}

#[tauri::command]
fn show_main(app: tauri::AppHandle) -> Result<(), String> {
    let window = app
        .get_window("main")
        .ok_or("找不到主窗口。恢复：重启客户端。")?;
    window.unminimize().map_err(|err| err.to_string())?;
    window.show().map_err(|err| err.to_string())?;
    window.set_focus().map_err(|err| err.to_string())?;
    Ok(())
}

#[tauri::command]
fn open_today(app: tauri::AppHandle) -> Result<(), String> {
    let window = app
        .get_window("main")
        .ok_or("找不到主窗口。恢复：重启客户端。")?;
    window.emit("open-today", true).map_err(|err| err.to_string())?;
    Ok(())
}

#[tauri::command]
fn quit_app(app: tauri::AppHandle) {
    app.exit(0);
}

/// 只打开 http/https。走 ShellExecute，不经过 cmd，避免链接里的符号被当成命令。
#[tauri::command]
fn open_link(url: String) -> Result<(), String> {
    let url = clean_http_url(&url)?;
    #[cfg(windows)]
    {
        let op = wide("open");
        let file = wide(&url);
        let code = unsafe {
            ShellExecuteW(
                std::ptr::null_mut(),
                op.as_ptr(),
                file.as_ptr(),
                std::ptr::null(),
                std::ptr::null(),
                1,
            )
        };
        if code <= 32 {
            return Err(format!("打不开链接（{code}）。恢复：复制地址到浏览器打开。"));
        }
        return Ok(());
    }
    #[cfg(not(windows))]
    {
        let _ = url;
        Err("只在 Windows 上打开链接。".into())
    }
}

fn clean_http_url(raw: &str) -> Result<String, String> {
    let url = raw.trim();
    if url.len() > 2000 {
        return Err("链接太长。恢复：复制地址到浏览器打开。".into());
    }
    if url.chars().any(|ch| ch.is_whitespace()) {
        return Err("链接里不能有空白。".into());
    }
    let lower = url.to_ascii_lowercase();
    if !(lower.starts_with("https://") || lower.starts_with("http://")) {
        return Err("只打开 http 或 https 链接。".into());
    }
    let rest = url.split_once("://").map(|(_, rest)| rest).unwrap_or("");
    let host = rest.split(['/', '?', '#']).next().unwrap_or("");
    if host.is_empty() || host.contains('@') {
        return Err("这个地址不能打开。恢复：复制到浏览器。".into());
    }
    Ok(url.to_string())
}

#[cfg(windows)]
fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(std::iter::once(0)).collect()
}

#[cfg(windows)]
#[link(name = "shell32")]
extern "system" {
    fn ShellExecuteW(
        hwnd: *mut std::ffi::c_void,
        lp_operation: *const u16,
        lp_file: *const u16,
        lp_parameters: *const u16,
        lp_directory: *const u16,
        n_show_cmd: i32,
    ) -> isize;
}

fn pick_free_port() -> u16 {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("无法分配端口");
    listener.local_addr().expect("读端口失败").port()
}

fn gen_token() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("tok{nanos:x}")
}

fn backend_exit_error(code: Option<i32>) -> String {
    if code == Some(73) {
        "该数据目录已有运行实例，请使用已有客户端或先退出该实例。".into()
    } else {
        "本地助手未能启动。请查看用户数据目录中的 backend.log，再重新启动客户端。开发版请确认 Python 与 Atlas 已安装。".into()
    }
}

fn wait_healthy(port: u16, process: &Mutex<Option<Child>>) -> Result<(), String> {
    let url = format!("http://127.0.0.1:{port}/health");
    for _ in 0..50 {
        if let Some(child) = process.lock().map_err(|err| err.to_string())?.as_mut() {
            if let Some(status) = child.try_wait().map_err(|err| err.to_string())? {
                return Err(backend_exit_error(status.code()));
            }
        }
        if let Ok(resp) = ureq::get(&url).timeout(Duration::from_millis(500)).call() {
            if resp.status() == 200 {
                return Ok(());
            }
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    Err(backend_exit_error(None))
}

// 后台轮询：全局鼠标落在任一命中区域内 -> 可交互；否则穿透。
fn start_cursor_poll(
    window: tauri::WebviewWindow,
    regions: std::sync::Arc<Mutex<Vec<Rect>>>,
    drag_lock: std::sync::Arc<AtomicBool>,
    menu_region: std::sync::Arc<Mutex<Option<Rect>>>,
) {
    std::thread::spawn(move || {
        let mut last_ignore: Option<bool> = None;
        let mut last_buttons = mouse_buttons();
        loop {
            std::thread::sleep(Duration::from_millis(30));
            let scale = window.scale_factor().unwrap_or(1.0);
            let cursor: PhysicalPosition<f64> = match window.cursor_position() {
                Ok(p) => p,
                Err(_) => continue,
            };
            let origin: PhysicalPosition<i32> = match window.outer_position() {
                Ok(p) => p,
                Err(_) => continue,
            };
            // 全局物理坐标 -> 窗口相对逻辑坐标
            let rx = (cursor.x - origin.x as f64) / scale;
            let ry = (cursor.y - origin.y as f64) / scale;
            let buttons = mouse_buttons();
            if last_buttons.0 && !buttons.0 && drag_lock.swap(false, Ordering::Relaxed) {
                let _ = window.emit("pet-pointer-released", ());
            }
            let dismiss = menu_region.lock().map(|menu| {
                pressed_outside_menu(menu.as_ref(), (rx, ry), last_buttons, buttons, drag_lock.load(Ordering::Relaxed))
            }).unwrap_or(false);
            last_buttons = buttons;
            if dismiss {
                if let Ok(mut menu) = menu_region.lock() { *menu = None; }
                let _ = window.emit("dismiss-pet-menu", ());
            }
            let inside = regions
                .lock()
                .map(|rs| rs.iter().any(|r| rx >= r.x && rx <= r.x + r.w && ry >= r.y && ry <= r.y + r.h))
                .unwrap_or(false);
            let ignore = !drag_lock.load(Ordering::Relaxed) && !inside;
            if last_ignore != Some(ignore) {
                last_ignore = Some(ignore);
                let _ = window.set_ignore_cursor_events(ignore);
            }
        }
    });
}

fn pressed_outside_menu(menu: Option<&Rect>, point: (f64, f64), before: (bool, bool), now: (bool, bool), selecting: bool) -> bool {
    let new_press = (now.0 && !before.0) || (now.1 && !before.1);
    let Some(rect) = menu else { return false; };
    new_press && !selecting && !(point.0 >= rect.x && point.0 <= rect.x + rect.w && point.1 >= rect.y && point.1 <= rect.y + rect.h)
}

#[cfg(windows)]
#[link(name = "user32")]
extern "system" { fn GetAsyncKeyState(key: i32) -> i16; }

fn mouse_buttons() -> (bool, bool) {
    #[cfg(windows)]
    { unsafe { return (GetAsyncKeyState(1) < 0, GetAsyncKeyState(2) < 0); } }
    #[cfg(not(windows))]
    { (false, false) }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let port = pick_free_port();
    let token = gen_token();

    let regions = std::sync::Arc::new(Mutex::new(Vec::<Rect>::new()));
    let regions_for_state = regions.clone();
    let drag_lock_for_state = std::sync::Arc::new(AtomicBool::new(false));
    let drag_lock_for_poll = drag_lock_for_state.clone();
    let menu_region = std::sync::Arc::new(Mutex::new(None));
    let menu_region_for_poll = menu_region.clone();

    tauri::Builder::default()
        .manage(BackendInfo { port, token })
        .manage(BackendProcess(Mutex::new(None)))
        .manage(BackendFailure(Mutex::new(None)))
        .manage(HitRegions(regions_for_state))
        .manage(DragLock(drag_lock_for_state))
        .manage(MenuRegion(menu_region.clone()))
        .setup(move |app| {
            let backend = app.state::<BackendInfo>();
            match start_backend(app.handle(), backend.port, &backend.token) {
                Ok(child) => *app.state::<BackendProcess>().0.lock().unwrap() = Some(child),
                Err(error) => *app.state::<BackendFailure>().0.lock().unwrap() = Some(error),
            }
            if let Some(win) = app.get_webview_window("pet") {
                let _ = win.set_always_on_top(true);
                let focus_window = win.clone();
                let focus_menu = menu_region.clone();
                win.on_window_event(move |event| {
                    if matches!(event, tauri::WindowEvent::Focused(false)) {
                        let was_open = focus_menu.lock().map(|mut rect| rect.take().is_some()).unwrap_or(false);
                        if was_open { let _ = focus_window.emit("dismiss-pet-menu", ()); }
                    }
                });
                start_cursor_poll(win, regions.clone(), drag_lock_for_poll.clone(), menu_region_for_poll.clone());
            }
            Ok(())
        })
        .invoke_handler(|invoke| {
            // Remote platform pages must never obtain backend tokens or invoke app commands.
            if !matches!(invoke.message.webview_ref().label(), "main" | "pet" | "pet-chat") {
                invoke.resolver.reject("远程网页不能调用本地助手。");
                return true;
            }
            let handler: fn(tauri::ipc::Invoke<tauri::Wry>) -> bool = tauri::generate_handler![
            open_video_login,
            capture_video_login,
            clear_video_login_window,
            open_side_browser,
            side_browser_layout,
            side_browser_action,
            set_hit_regions,
            set_pet_menu_region,
            backend_info,
            set_pet_visible,
            set_pet_chat_visible,
            toggle_pet_chat,
            get_pet_chat_state,
            set_pet_chat_pinned,
            set_drag_lock,
            nudge_pet,
            show_main,
            open_today,
            open_link,
            quit_app
        ];
            handler(invoke)
        })
        .build(tauri::generate_context!())
        .expect("构建 Tauri 应用失败")
        .run(|app, event| {
            if let tauri::RunEvent::ExitRequested { .. } = event {
                if let Some(state) = app.try_state::<BackendProcess>() {
                    if let Ok(mut guard) = state.0.lock() {
                        if let Some(mut child) = guard.take() {
                            let backend = app.state::<BackendInfo>();
                            stop_backend(&mut child, &backend);
                        }
                    }
                }
            }
        });
}

fn stop_backend(child: &mut Child, info: &BackendInfo) {
    // 先通知后端关闭自有 CLI 消费者，不能杀掉其他消费者共用的 bus。
    let url = format!("http://127.0.0.1:{}/shutdown", info.port);
    let _ = ureq::get(&url)
        .set("Authorization", &format!("Bearer {}", info.token))
        .timeout(Duration::from_secs(2))
        .call();
    for _ in 0..60 {
        if matches!(child.try_wait(), Ok(Some(_))) { return; }
        std::thread::sleep(Duration::from_millis(100));
    }
    let _ = child.kill();
    let _ = child.wait();
}

#[cfg(test)]
mod menu_tests {
    use super::*;

    #[test]
    fn exited_duplicate_backend_reports_ownership_conflict() {
        let mut command = Command::new("python");
        command.args(["-c", "raise SystemExit(73)"]).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let process = Mutex::new(Some(command.spawn().unwrap()));
        let started = std::time::Instant::now();
        assert!(wait_healthy(pick_free_port(), &process).unwrap_err().contains("已有运行实例"));
        assert!(started.elapsed() < Duration::from_secs(4));
        assert!(!backend_exit_error(Some(1)).contains("已有运行实例"));
    }

    #[test]
    fn backend_exits_after_authenticated_shutdown_without_forced_kill() {
        use std::io::BufRead;
        let script = r#"
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/shutdown' or self.headers.get('Authorization') != 'Bearer test-only':
            self.send_response(403); self.end_headers(); return
        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
        threading.Thread(target=self.server.shutdown, daemon=True).start()
    def log_message(self, *args): pass
server = HTTPServer(('127.0.0.1', 0), Handler)
print(server.server_port, flush=True)
server.serve_forever()
server.server_close()
"#;
        let mut command = Command::new("python");
        command.args(["-u", "-c", script]).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let mut child = command.spawn().expect("本机验证需要 Python");
        let mut port_line = String::new();
        std::io::BufReader::new(child.stdout.take().unwrap()).read_line(&mut port_line).unwrap();
        let info = BackendInfo { port: port_line.trim().parse().unwrap(), token: "test-only".into() };
        let started = std::time::Instant::now();
        stop_backend(&mut child, &info);
        assert!(child.try_wait().unwrap().unwrap().success());
        assert!(started.elapsed() < Duration::from_secs(4));
    }

    #[test]
    fn transparent_area_and_other_windows_dismiss_on_new_press() {
        let menu = Rect { x: 20.0, y: 20.0, w: 100.0, h: 100.0 };
        assert!(pressed_outside_menu(Some(&menu), (0.0, 0.0), (false, false), (true, false), false));
        assert!(pressed_outside_menu(Some(&menu), (-200.0, 800.0), (false, false), (false, true), false));
        assert!(!pressed_outside_menu(Some(&menu), (30.0, 30.0), (false, false), (true, false), false));
    }

    #[test]
    fn selection_drag_and_held_buttons_keep_menu_open() {
        let menu = Rect { x: 20.0, y: 20.0, w: 100.0, h: 100.0 };
        assert!(!pressed_outside_menu(Some(&menu), (-5.0, -5.0), (false, false), (true, false), true));
        assert!(!pressed_outside_menu(Some(&menu), (-5.0, -5.0), (true, false), (true, false), false));
        assert!(!pressed_outside_menu(None, (0.0, 0.0), (false, false), (true, false), false));
    }
}

fn start_backend(app: &tauri::AppHandle, port: u16, token: &str) -> Result<Child, String> {
    let (python, working_dir) = backend_location()?;
    let data_dir = if let Some(value) = std::env::var_os("DESK_COMPANION_DATA_DIR") {
        std::path::PathBuf::from(value)
    } else if cfg!(debug_assertions) {
        working_dir.clone()
    } else {
        app.path().local_data_dir().map_err(|err| err.to_string())?.join("DeskCompanion")
    };
    std::fs::create_dir_all(&data_dir).map_err(|err| format!("无法创建用户数据目录：{err}"))?;
    let assets_dir = if cfg!(debug_assertions) { working_dir.join("client/public") } else { data_dir.join("assets") };
    std::fs::create_dir_all(&assets_dir).map_err(|err| err.to_string())?;
    let log_path = data_dir.join("backend.log");
    let log = std::fs::OpenOptions::new().create(true).append(true).open(&log_path).map_err(|err| err.to_string())?;
    let mut command = Command::new(python);
    command.args(["-u", "-m", "desk_companion.local_api.server", "--port"])
        .arg(port.to_string()).arg("--token").arg(token)
        .current_dir(working_dir)
        .env("DESK_COMPANION_DATA_DIR", &data_dir)
        .env("DESK_COMPANION_ASSET_DIR", &assets_dir)
        .env("PYTHONUTF8", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::from(log.try_clone().map_err(|err| err.to_string())?))
        .stderr(Stdio::from(log));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    command.spawn().map_err(|err| format!("无法启动本地助手：{err}。请完整解压发布包，保留 runtime 文件夹。日志：{}", log_path.display()))
}

#[cfg(debug_assertions)]
fn backend_location() -> Result<(std::path::PathBuf, std::path::PathBuf), String> {
    Ok(("python".into(), std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")))
}

#[cfg(not(debug_assertions))]
fn backend_location() -> Result<(std::path::PathBuf, std::path::PathBuf), String> {
    let exe = std::env::current_exe().map_err(|err| err.to_string())?;
    let directory = exe.parent().ok_or("无法确定程序目录")?.to_path_buf();
    let python = directory.join("runtime/python.exe");
    if !python.is_file() { return Err("运行环境缺失。请完整解压发布包，保留程序入口旁的 runtime 文件夹。".into()); }
    Ok((python, directory))
}
