//! Real Tauri event-loop probe, no backend, model calls or user data.
use desk_companion_client_lib::main_window;
use std::{
    path::PathBuf,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tauri::Manager;

fn main() {
    let work = PathBuf::from(std::env::args_os().nth(1).expect("需要新的绝对验证目录"));
    assert!(
        work.is_absolute() && !work.exists(),
        "验证目录必须是新的绝对路径"
    );
    std::fs::create_dir_all(&work).unwrap();
    let mut context = tauri::generate_context!();
    context.config_mut().identifier = "cn.deskcompanion.verify212.lifecycle".into();
    context.config_mut().app.windows.clear();
    let outcome = Arc::new(Mutex::new(None));
    let observed = outcome.clone();
    tauri::Builder::default().setup(move |app| {
        let window = tauri::WebviewWindowBuilder::new(app, "main", tauri::WebviewUrl::External("about:blank".parse().unwrap()))
            .title("tebb #212 window lifecycle probe").inner_size(420.,180.)
            .data_directory(work.join("webview")).build()?;
        main_window::retain_on_close(&window);
        let handle = app.handle().clone();
        let outcome = outcome.clone();
        std::thread::spawn(move || {
            let result = (|| -> Result<usize,String> {
                for _ in 0..3 {
                    window.close().map_err(|error|error.to_string())?;
                    let deadline = Instant::now()+Duration::from_secs(10);
                    loop {
                        if handle.get_webview_window("main").is_none() { return Err("关闭请求销毁了主窗口".into()); }
                        if !window.is_visible().map_err(|error|error.to_string())? { break; }
                        if Instant::now()>=deadline { return Err("关闭请求未收起主窗口".into()); }
                        std::thread::sleep(Duration::from_millis(20));
                    }
                    main_window::show(&handle)?;
                    if !window.is_visible().map_err(|error|error.to_string())? { return Err("重开后窗口不可见".into()); }
                }
                Ok(3)
            })();
            let proof=match &result { Ok(rounds)=>serde_json::json!({"ok":true,"rounds":rounds,"real_tauri_events":true,"backend_started":false,"pid":std::process::id(),"window_label":"main"}),Err(error)=>serde_json::json!({"ok":false,"error":error,"pid":std::process::id()}) };
            std::fs::write(work.join("result.json"),serde_json::to_vec_pretty(&proof).unwrap()).unwrap();
            *outcome.lock().unwrap()=Some(result.is_ok());
            handle.exit(if result.is_ok(){0}else{1});
        });
        Ok(())
    }).build(context).unwrap().run(|_,_|{});
    assert_eq!(
        *observed.lock().unwrap(),
        Some(true),
        "原生窗口生命周期验证失败"
    );
}
