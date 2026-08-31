//! GameTR Tauri 壳：管理 Python sidecar（gt-core）的生命周期。
//!
//! Spike 1（路线图 1.3）最小验证 → M4 泛化：
//! - 启动时 spawn sidecar，每 500ms 重试 `core.ping` 直到成功（握手）
//! - `rpc_request(method, params)`：任意 JSON-RPC 方法，按 id 配对响应（M4 前端所有 RPC 入口）
//! - 通知转发：stdout 里的无 id 通知帧（progress 等）emit 到 webview（前端事件订阅）
//! - 外部杀掉 core 进程 → Terminated 事件 → 自动重启并恢复
//! - 应用退出时 kill sidecar（EOF 兜底：进程退出管道关闭，Python 侧 readline 返回 EOF）

use std::sync::atomic::{AtomicU32, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tauri::{AppHandle, Emitter, Manager, State};
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
    /// sidecar 启动时间（检测「启动即退出」循环）
    last_start: Arc<Mutex<Option<Instant>>>,
    /// 连续快速退出计数（指数退避 + 上限，防循环重启刷日志/耗资源）
    quick_exits: Arc<AtomicU32>,
}

impl SidecarState {
    fn new() -> Self {
        Self {
            child: Arc::new(Mutex::new(None)),
            lines_tx: tokio::sync::broadcast::channel(64).0,
            next_id: Arc::new(AtomicU64::new(0)),
            last_start: Arc::new(Mutex::new(None)),
            quick_exits: Arc::new(AtomicU32::new(0)),
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
    *state.last_start.lock().unwrap() = Some(Instant::now());

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
                    // 外部杀 core → 清句柄 → 退避/重启（M0 验收③；防「启动即退出」循环）
                    let elapsed = state2.last_start.lock().unwrap()
                        .map(|t| t.elapsed()).unwrap_or(Duration::MAX);
                    let was_quick = elapsed < Duration::from_secs(5);
                    eprintln!(
                        "[sidecar] 进程终止(code={:?}, signal={:?}, 运行 {:?})",
                        payload.code, payload.signal, elapsed
                    );
                    *state2.child.lock().unwrap() = None;
                    if was_quick {
                        // 快速退出（启动 5s 内）→ 指数退避；连续 8 次停止（避免循环刷日志/耗资源）
                        let qe = state2.quick_exits.fetch_add(1, Ordering::Relaxed) + 1;
                        if qe >= 8 {
                            eprintln!("[sidecar] 连续快速退出 {qe} 次，停止自动重启（可手动触发 ping 恢复）");
                            break;
                        }
                        let delay = Duration::from_secs(1u64 << qe.min(5)); // 1,2,4,8,16,32s
                        eprintln!("[sidecar] 快速退出 {qe} 次，{delay:?} 后重启");
                        tokio::time::sleep(delay).await;
                    } else {
                        state2.quick_exits.store(0, Ordering::Relaxed); // 正常运行退出，重置计数
                    }
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

/// 通知转发：把 stdout 广播里的**无 id 通知帧**（JSON-RPC 通知，如 progress）emit 到 webview。
/// 常驻订阅（init 时启动一次），sidecar 重启后 reader 仍写同一 broadcast，无需重订阅。
/// 前端 `listen('rpc-notification', ...)` 订阅（启动即订阅，配合 translate.status 兜底丢失）。
fn spawn_notification_forwarder(app: AppHandle, state: SidecarState) {
    tauri::async_runtime::spawn(async move {
        let mut rx = state.lines_tx.subscribe();
        loop {
            match rx.recv().await {
                Ok(line) => {
                    let Ok(v) = serde_json::from_str::<serde_json::Value>(&line) else {
                        continue;
                    };
                    // 通知 = 有 method 且无 id（JSON-RPC 2.0 规范）；请求响应（有 id）由 rpc_request 配对
                    if v.get("method").is_some() && v.get("id").is_none() {
                        let _ = app.emit("rpc-notification", &v);
                    }
                }
                // 无发送者（broadcast 关闭）或 Lag 追赶，继续等
                Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => continue,
                Err(_) => {
                    eprintln!("[sidecar] 通知转发通道关闭");
                    break;
                }
            }
        }
    });
}

/// 发一次 JSON-RPC 请求并等 id 匹配的响应（超时）。
/// 进程不存在时先补 spawn（兜底 Terminated 之外的场景）。
async fn do_rpc(
    app: &AppHandle,
    state: &SidecarState,
    method: &str,
    params: Option<serde_json::Value>,
    timeout: Duration,
) -> Result<serde_json::Value, String> {
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
    let mut req = serde_json::json!({"jsonrpc": "2.0", "id": id, "method": method});
    if let Some(p) = params {
        req["params"] = p;
    }
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
    let deadline = Instant::now() + timeout;
    loop {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Err(format!("{method} 超时"));
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
                    return Err(format!("{method} 错误响应: {err}"));
                }
            }
            // 广播被追上（Lag），忽略继续等
            Ok(Err(tokio::sync::broadcast::error::RecvError::Lagged(_))) => continue,
            _ => return Err(format!("{method} 超时")),
        }
    }
}

/// 前端 RPC 入口（M4）：任意 JSON-RPC 方法。invoke("rpc_request", { method, params })。
/// 长任务（translate.start）在核心侧立即返回 task_id，不阻塞。
#[tauri::command]
async fn rpc_request(
    app: AppHandle,
    state: State<'_, SidecarState>,
    method: String,
    params: Option<serde_json::Value>,
) -> Result<serde_json::Value, String> {
    do_rpc(&app, &state, &method, params, Duration::from_secs(30)).await
}

/// 前端入口：ping core 并附加往返耗时（M0 验收②）。
#[tauri::command]
async fn core_ping(
    app: AppHandle,
    state: State<'_, SidecarState>,
) -> Result<serde_json::Value, String> {
    let start = Instant::now();
    let mut result = do_rpc(&app, &state, "core.ping", None, Duration::from_secs(2)).await?;
    let roundtrip_ms = start.elapsed().as_millis() as u64;
    if let Some(obj) = result.as_object_mut() {
        obj.insert("roundtrip_ms".into(), serde_json::json!(roundtrip_ms));
    }
    Ok(result)
}

/// webview 收到的通知负载类型（序列化给前端 listen）。
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        // 单实例：防止用户开多个 desktop.exe（多个 sidecar 竞争导致异常/资源占用）
        .plugin(tauri_plugin_single_instance::init(|_app, _args, _cwd| {}))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(SidecarState::new())
        .setup(|app| {
            let state = app.state::<SidecarState>().inner().clone();
            let handle = app.handle().clone();
            spawn_notification_forwarder(handle.clone(), state.clone());
            tauri::async_runtime::spawn(async move {
                let mut attempts = 0u32;
                loop {
                    match do_rpc(&handle, &state, "core.ping", None, Duration::from_secs(2)).await {
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
        .invoke_handler(tauri::generate_handler![rpc_request, core_ping])
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
