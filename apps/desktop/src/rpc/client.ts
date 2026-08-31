/**
 * GameTR 前端 RPC 客户端（M4 基础）。
 *
 * - `rpc(method, params)`：任意 JSON-RPC 方法，走 Tauri `rpc_request` command（Rust 侧按 id 配对）
 * - `onNotification(cb)`：订阅服务端推送（progress 等无 id 通知帧，Rust 侧 emit 到 webview）
 *
 * 启动即订阅：App 挂载时调一次 `ensureSubscribed()`，避免丢失 sidecar 重启/翻译中的通知。
 */

import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

export interface RpcNotification {
  jsonrpc: string;
  method: string;
  params: Record<string, unknown>;
}

/** 调任意 JSON-RPC 方法（Rust `rpc_request`）。返回 result；error 抛字符串。 */
export async function rpc<T = unknown>(method: string, params?: object): Promise<T> {
  return invoke<T>("rpc_request", { method, params });
}

let subscribed = false;
const subscribers = new Set<(n: RpcNotification) => void>();

/** 订阅服务端通知（progress 等）。返回退订函数。幂等订阅底层事件。 */
export function onNotification(cb: (n: RpcNotification) => void): () => void {
  ensureSubscribed();
  subscribers.add(cb);
  return () => {
    subscribers.delete(cb);
  };
}

/** 确保已订阅 Tauri 事件（App 挂载时调一次；启动即订阅防丢通知）。 */
export function ensureSubscribed(): void {
  if (subscribed) return;
  subscribed = true;
  void listen<RpcNotification>("rpc-notification", (event) => {
    for (const fn of subscribers) fn(event.payload);
  });
}
