# ADR-0007：M3 翻译流水线架构（asyncio 服务 + Provider 抽象 + 任务管理）

- 状态：已接受（2026-08-28，M3 实现完成，真实 DeepSeek 验证）
- 日期：2026-08-28
- 相关：ADR-0002（JSON-RPC/NDJSON）、ADR-0005（AI 结构化输出）、M4 前端、M6 Ren'Py

## 背景（Context）

M3 引入分钟级长任务（AI 翻译），现有 server.py 是**同步串行**（读一行→handle→写一行）：
translate.start 若同步跑完，pause/cancel 永远排不上队，progress 通知也无处落地。
另一个缺口：占位符保护器语法在插件域（RPGMV `\N[1]`），但 M3 流水线的 Protector
阶段在核心——核心必须能按契约调用插件能力。

## 决策（Decision）

### 1. serve_stdio asyncio 化（单事件循环 + 单 writer）

- `serve_stdio` 保持**同步签名**（`asyncio.run` 内部驱动）→ 现有测试/CLI 全兼容
- stdin 读行走 executor 线程（不阻塞事件循环）；stdout 由**单 writer 协程**从队列
  串行写出（响应 + progress 通知共用，保证 NDJSON 帧原子性、通知不打断响应流）
- MethodRegistry 加 `handle_async`：handler 为协程时 await（providers.test/translate.start）
- 同步 handler 直接在 loop 线程跑（SQLite 毫秒级）；长任务由 translate.start
  `create_task` 后台协程，经 `ctx['notify']` 发通知
- **拒绝后台线程方案**：需全局写锁 + 跨线程排队 + 独立 loop + SQLite 连接归属处置，
  机械更多、错误面更大；asyncio 单线程天然帧原子

### 2. 翻译流水线 = 引擎无关的 Pipe-And-Filter

```
ContextBatcher(同文件+order排序+双上限) → Protector(插件契约) → Provider.translate_batch
→ Validator(占位符序列/漏译/空, 重试1次) → Restorer → Persister(upsert_translations)
```

- 插件 API **加性演进** v1.1：可选 `protect/restore/has_protected`（feature-detect，
  缺省身份保护器）；api_version 从精确相等改 **major 匹配 + minor >= 最低支持**
- ContextBatcher 分组语义**引擎无关**（机制在核心，语义在插件 context_json 的
  file_path/order/speaker）→ M6 Ren'Py 复用同一 batcher
- `upsert_translations`：ON CONFLICT 的 UPDATE 里 `WHERE status != 4`（CONFIRMED）在
  **同一条 SQL 原子**完成——防「跨协程先查后改」竞态把人工确认打回 MACHINE

### 3. Provider 层（内置注册，拒绝目录扫描 YAGNI）

- `TranslationProvider` Protocol 极窄：`async translate_batch` + `test`
- MockProvider（确定性伪翻译，保留占位符）+ OpenAICompatibleProvider（httpx async，
  **三形态响应解析**——spike4 实测模型跟随 user JSON 模板，translations/items/顶层数组都兼容）
- api_key 规范路径 = **环境变量注入**（M4 钥匙串→Tauri spawn sidecar 时注入），
  RPC 日志 `_RpcLogger` 单点递归掩码（api_key/token/...），密钥不进 RPC 参数

### 4. 任务管理 + 断点续翻

- 任务态（running/paused/cancelled/done/error）在任务表，**不进项目状态机**
- 断点续翻：translate.start 只选未翻译条目（translation is null），已翻自然跳过
- 取消在**批边界**检查（任务态≠running 即停），不留半写状态；cancel 后 partial 保留
- cache_key 由调用方组装（含 provider/model/style/glossary 哈希），本次未落 cache 表
  （translate_cache 建表未用，M3.5/M4 按需启用）

## 后果（Consequences）

- M4 前端：进度条订阅 `progress` 通知（一个 method 覆盖全部任务生命周期，看 status）；
  翻译控制台依赖 M3 的 translate.*/providers.*（已定稿协议）
- M6 Ren'Py：Protector 由插件提供（新插件实现自己的占位符语法）；ContextBatcher 复用
- 已知限制：resume 不重启已停止协程（用户需重新 start 续翻）；few-shot 上下文注入未接入
  （fill_speaker_and_few_shot 已实现未调用）；提示词评测集（Phase 6）未建
