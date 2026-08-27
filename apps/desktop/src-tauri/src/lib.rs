//! GameTR Tauri 壳：管理 Python sidecar（gt-core）的生命周期。
//!
//! Spike 1（路线图 1.3）最小验证，不做业务：
//! - 启动时 spawn sidecar，每 500ms 重试 `core.ping` 直到成功（握手）
//! - 通过 stdio 与 sidecar 通信（JSON-RPC 2.0 / NDJSON，见 ADR-0002）
//! - 外部杀掉 core 进程 → Terminated 事件 → 自动重启并恢复（M0 验收③）
//! - 应用退出时 kill sidecar（EOF 兜底：进程退出管道关闭，Python 侧 readline 返回 EOF）

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, State};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// sidecar 状态：进程句柄（含 stdin）+ stdout 行的广播通道 + 请求 id 自增。
#[derive(Clone)]
struct SidecarState {
    /// 当前进程；None 表示进程已退出待重启
    child: Arc<Mutex<Option<CommandChild>>>,
    /// stdout 逐行广播（shell 插件默认按 \n/\r 分行触发 Stdout 事件）
    lines_tx: tokio::sync::broadcast::Sender<String>,
    /// JSON-RPC 请求 id 自增（进程内单调即可）
    next_id: Arc<AtomicU64>,
}

impl SidecarState {
    fn new() -> Self {
        Self {
            child: Arc::new(Mutex::new(None)),
            lines_tx: tokio::sync::broadcast::channel(64).0,
            next_id: Arc::new(AtomicU64::new(0)),
        }
    }
}

/// 启动 sidecar 并挂 reader 任务（stdout 行 → 广播；进程被杀 → 自动重启）。
fn spawn_sidecar(app: &AppHandle, state: &SidecarState) -> Result<(), String> {
    let (mut rx, child) = app
        .shell()
        .sidecar("gt-core")
        .map_err(|e| format!("sidecar 创建失败: {e}"))?
        .spawn()
        .map_err(|e| format!("sidecar 启动失败: {e}"))?;
    *state.child.lock().unwrap() = Some(child);

    let state2 = state.clone();
    let app2 = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    // 非 raw 模式下每个 Stdout 事件是一整行（不含 \n/\r）
                    let line = String::from_utf8_lossy(&line);
                    let line = line.strip_suffix('\r').unwrap_or(&line).to_string();
                    let _ = state2.lines_tx.send(line);
                }
                // 核心日志走 stderr；M0 忽略，M4 接到日志查看器
                CommandEvent::Stderr(_) => {}
                CommandEvent::Error(err) => {
                    eprintln!("[sidecar] 读取错误: {err}");
                    break;
                }
                CommandEvent::Terminated(payload) => {
                    // 外部杀 core → 清句柄 → 自动重启并恢复（M0 验收③）
                    eprintln!(
                        "[sidecar] 进程终止(code={:?}, signal={:?})，自动重启",
                        payload.code, payload.signal
                    );
                    *state2.child.lock().unwrap() = None;
                    if let Err(e) = spawn_sidecar(&app2, &state2) {
                        eprintln!("[sidecar] 重启失败: {e}（下次 ping 会再试）");
                    }
                    break;
                }
                // 枚举 non_exhaustive，兜底未来新增事件
                _ => {}
            }
        }
    });
    Ok(())
}

/// 发一次 core.ping 并等 id 匹配的响应（2s 超时）。
/// 进程不存在时先补 spawn（兜底 Terminated 之外的场景）。
async fn do_ping(app: &AppHandle, state: &SidecarState) -> Result<serde_json::Value, String> {
    {
        let guard = state.child.lock().unwrap();
        if guard.is_none() {
            drop(guard);
            spawn_sidecar(app, state)?;
        }
    }
    // 先订阅再发请求：广播在无订阅者时丢消息，订阅晚于写入会错过响应
    let mut resp_rx = state.lines_tx.subscribe();
    let id = state.next_id.fetch_add(1, Ordering::Relaxed);
    let req = serde_json::json!({"jsonrpc": "2.0", "id": id, "method": "core.ping"});
    {
        let mut guard = state.child.lock().unwrap();
        match guard.as_mut() {
            Some(child) => {
                child
                    .write(format!("{req}\n").as_bytes())
                    .map_err(|e| format!("写入 sidecar 失败: {e}"))?;
            }
            None => return Err("sidecar 尚未启动".into()),
        }
    }
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Err("core.ping 超时".into());
        }
        match tokio::time::timeout(remaining, resp_rx.recv()).await {
            Ok(Ok(line)) => {
                let Ok(v) = serde_json::from_str::<serde_json::Value>(&line) else {
                    continue;
                };
                if v.get("id") != Some(&serde_json::json!(id)) {
                    continue;
                }
                if let Some(result) = v.get("result") {
                    return Ok(result.clone());
                }
                if let Some(err) = v.get("error") {
                    return Err(format!("core.ping 错误响应: {err}"));
                }
            }
            // 广播被追上（Lag ），忽略继续等
            Ok(Err(tokio::sync::broadcast::error::RecvError::Lagged(_))) => continue,
            _ => return Err("core.ping 超时".into()),
        }
    }
}

/// 前端入口：ping core 并附加往返耗时（M0 验收②：窗口显示 ping 往返耗时）。
#[tauri::command]
async fn core_ping(
    app: AppHandle,
    state: State<'_, SidecarState>,
) -> Result<serde_json::Value, String> {
    let start = Instant::now();
    let mut result = do_ping(&app, &state).await?;
    let roundtrip_ms = start.elapsed().as_millis() as u64;
    if let Some(obj) = result.as_object_mut() {
        obj.insert("roundtrip_ms".into(), serde_json::json!(roundtrip_ms));
    }
    Ok(result)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .manage(SidecarState::new())
        .setup(|app| {
            let state = app.state::<SidecarState>().inner().clone();
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let mut attempts = 0u32;
                loop {
                    match do_ping(&handle, &state).await {
                        Ok(_) => {
                            eprintln!("[sidecar] 握手成功");
                            break;
                        }
                        Err(e) => {
                            attempts += 1;
                            eprintln!("[sidecar] 握手失败({attempts}): {e}");
                            if attempts >= 30 {
                                eprintln!("[sidecar] 握手重试达上限（30×500ms），GUI 继续运行，功能不可用");
                                break;
                            }
                            tokio::time::sleep(Duration::from_millis(500)).await;
                        }
                    }
                }
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![core_ping])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        if let tauri::RunEvent::Exit = event {
            if let Some(state) = app_handle.try_state::<SidecarState>() {
                let child = state.child.lock().unwrap().take();
                if let Some(child) = child {
                    let _ = child.kill();
                }
            }
        }
    });
}
