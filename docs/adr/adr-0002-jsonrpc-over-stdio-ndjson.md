# ADR-0002：为何 JSON-RPC / stdio / NDJSON

- 状态：已接受
- 日期：2026-08-27
- 相关：ADR-0001（Tauri + Sidecar）

## 背景（Context）

Rust 壳与 Python 核心之间需要一种跨语言、可校验、可回放、易调试的进程间协议。

关键需求来自产品性质：**无遥测离线工具**，用户排障只能靠本机日志（M1 的 "RPC 全量日志是排障手段"）。协议必须对日志友好：每一条消息都能逐行落盘、可 grep、可回放。

## 决策（Decision）

**JSON-RPC 2.0 over stdio，NDJSON 帧**（一行一个完整 JSON 消息）：

- 请求/响应/通知都走 stdin/stdout，一"帧"=一行 JSON
- 日志一律走 stderr 与日志文件，**stdout 保持纯净**（协议通道不能被日志污染）
- 强制 UTF-8 编码 + `\n` 行尾（Windows 中文区域默认 cp936，不强制会乱码/CRLF 破坏帧）
- >1MB 的大消息禁止，分页规避
- 错误码表进 protocol（-32700 标准码 + -32001 引擎不支持等应用码）

## 备选方案与拒绝理由

| 方案 | 拒绝理由 |
| :--- | :--- |
| gRPC / HTTP over localhost | 端口分配与防火墙问题；二进制帧日志不可读；自签 TLS 麻烦 |
| WebSocket | 需要端口 + 握手，对单机双进程是过度设计 |
| msgpack / 自定义二进制 | 日志不可读，调试困难 |
| stdio + 逐行 JSON 的优点 | 零端口零冲突；逐行天然背压友好；每行一个完整 JSON → 逐行日志 → grep/回放；Python 只需标准库 json，Rust 侧 shell 插件管道现成 |

## 后果（Consequences）

- 消息内不能有换行（JSON 字符串中的换行必须转义为 `\n`）
- 大数组用分页（entries.list 带 page/page_size，单页上限 2000）
- 已实现并验证（M0）：`core.ping` 往返、UTF-8 中文/日文、通知不应答、EOF 优雅退出、RPC 日志逐行落 `logs/rpc-*.ndjson`
- M3 引入并发时在方法注册表之上加 asyncio 层，帧格式不变

> 经验证：Python 侧必须 `sys.stdin/stdout.reconfigure(encoding="utf-8", newline="\n")`，否则 Windows 下 CRLF 与 cp936 会破坏帧。
