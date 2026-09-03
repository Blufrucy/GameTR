# AGENTS.md — GameTR 项目上下文记忆

> 给 AI 协作者的工作记忆。改动/决策/背景优先更新本文件与 `docs/ai/context/`、`docs/adr/`。

## 项目是什么

桌面游戏翻译器：Tauri v2（Rust 壳 + React 前端）+ Python 核心 `gt-core`（sidecar，stdio JSON-RPC）。
目标引擎：RPG Maker MV/MZ（M2）、Ren'Py（M6）。离线优先、AI 翻译 + 人工双栏校对。
总路线图见 `游戏翻译器总路线图.md`（当前处于 **M4** UI 打磨——编辑器为主工作区，翻译/回写=命令弹窗，详见 `docs/ai/context/m4.md`）。

## 架构决策（已定稿，勿改）

- **ADR-0001** Tauri + Python sidecar：Web 前端（5万行表格）、Python 生态（解包/AI）、包体积<100MB
- **ADR-0002** JSON-RPC 2.0 over stdio / NDJSON：stdout 纯净、UTF-8、一行一消息、日志走 stderr+文件
- **ADR-0003** locator 对核心不透明字符串（插件解析，核心只存不解析）；**写回锚点=字节区间**：extract 记录字符串字面量区间 `[start,end)` 存 `context_json.char_ranges`（可多个，401 拼接）+ `segments`（每区间原文），write_back 切片替换，与 locator 分工（语义位置 vs 物理位置）
- **ADR-0004** RPG Maker write_back **正解 = 字节区间替换**（`locate_strings`+`apply_text_swap`，格式免疫）；`serialize_rpgm`（重序列化）降级为黄金样本"空翻译 diff=0"校验器，非 write_back 实现；**非** indent=2（真实 MZ+MV 双工程验证）
- **ADR-0005** AI 结构化输出：占位符保护器是硬要求，`response_format` 默认 json_schema（实测待 API Key）
- **ADR-0006** SQLite 存储：单连接+WAL、迁移目录序号连续、FTS5 trigram+LIKE降级、单条严格状态机 vs 批量跳过CONFIRMED（M1）
- **ADR-0007** M3 翻译流水线：serve_stdio asyncio 化（同步签名+单writer保帧原子）；流水线引擎无关（ContextBatcher/Protector按插件契约）；Provider 内置注册（Mock+OpenAI兼容三形态解析）；api_key 环境注入+RPC日志单点脱敏；任务态不进项目状态机、批边界取消、断点续翻=跳过已翻译

## 代码结构速览

- `core/` — Python 包 `gt-core`（uv workspace member，根 `uv sync` 可编辑安装）
  - `gt_core/rpc/server.py`：NDJSON 帧 + MethodRegistry（装饰器注册，支持 schema 参数校验）；`__main__.py` CLI（serve/self-test）
  - `gt_core/rpc/models.py`、`plugin_manifest.py`：**协议生成物，禁止手改**，改 `protocol/schema/` 后 `pnpm protocol`
  - `gt_core/ir.py`：稳定 ID `sha1(engine_id+locator+source[:256])[:16]`；locator 契约
  - `gt_core/pipeline.py`：项目/条目状态机（非法迁移抛异常）+ 批量守卫（CONFIRMED 不可批量改）
  - `gt_core/project/`：db.py（WAL 连接）/ migrator.py（版本连续迁移）/ repo.py（DAO）/ project.py（门面）
  - `gt_core/rpc/params.py`：方法参数 pydantic 模型（extra=forbid）
  - `gt_core/providers/`：Provider 层（Mock/OpenAICompatible 三形态解析/ProviderManager）
  - `gt_core/translate/`：流水线（batcher 分组/stages 占位符/tasks 任务表/pipeline 编排/runner 后台任务）
- `apps/desktop/` — Tauri v2 + React
  - `src-tauri/src/lib.rs`：sidecar 生命周期（spawn/握手/心跳重启/kill）+ 单实例插件；`rpc_request`/`rpc-notification`（帧转发 webview）
  - `src/App.tsx` = 唯一主面（Editor 表格）+ MenuBar/Sidebar/StatusBar；翻译/回写/模型 API 全部「按钮→Modal 确认」；导入进度=唯一阻塞覆盖层
  - `src/components/`：VirtualTable（TanStack+react-virtual，5 万行）、EditEntryModal、TranslateModal、WritebackModal、ApiKeyModal、MenuBar/Sidebar/StatusBar/ui（自研 Button/Select/Modal）
  - `src/store/app.ts`：zustand 全局态。**View 体系已删**（无独立视图）；EntryRow 含 `edited`(人工改过) 与 `status`(1待译/2机翻/4已确认) 正交 + `machine_text`(机翻基线，null=从未机翻)
  - `src/rpc/models.ts`、`plugin-manifest.ts`：协议生成物，禁止手改
- `protocol/schema/` — 单一事实源（common/rpc-methods/plugin-manifest）
- `plugins/rpgmv/` — RPGMV 插件（detect/extract/write_back + 占位符保护器；ranges.py 字节区间）
- `tests/golden/rpgmv/` — 黄金样本（make_sample 生成真实格式 + 三测试 + expected.json 快照）
- `tests/spikes/` — M0 Spike 实验；`tests/e2e/m1_flow.py` — M1 验收脚本（10 万条全流程）

## 已验证的事实（不要重复验证）

- core RPC + M1：**105 个 pytest 通过**，核心覆盖率 **93%**；mypy/ruff 干净
- **M1 全流程验收**：`python tests/e2e/m1_flow.py`（10 万条插入 ~3.5s，12 项 PASS，含 RPC 日志回放）
- **M1 性能**：5 万条分页 <50ms、FTS/LIKE 搜索 <100ms（test_perf）
- **M1 review 修复**：4 维度审查 14 个确认缺陷已修复（IR 碰撞/迁移原子性/未来版本守卫/通知应答/null 语义/IN 分块/FTS 重索引 等），回归在 test_m1_fixes.py
- **Spike 1 端到端**：Tauri 启动 spawn sidecar → 握手成功；**杀 gt-core 进程 → 自动重启恢复**
- **Spike 2 真实工程验证**（2026-08-28）：真实 MZ（oriontest/rmmz 1.9.0，17 文件）+ 真实 MV（False Awakening，83 文件）`serialize_rpgm` 100% 字节复刻；真实格式=JS 紧凑+null 数组展开，**推翻合成样本 indent=2 假设**；三格式差异（空 events 展开/CRLF/插件 Doodads indent=2）逼出结论——**write_back 改用字节区间替换**（17580 字符串验证无损），重序列化降级校验器
- **Spike 3 实测**（2026-08-28 DeepSeek v4-flash）：默认模式非法JSON率 **33%**（模型空输出）→ **response_format 必须开**；json_object 全指标 0%；48 条占位符样本 0 破坏；吞吐 ~2.5 条/s（详 ADR-0005）。DeepSeek 旧模型名 deepseek-chat/reasoner 已于 2026-07-24 停用，不支持 json_schema strict
- **M2 完成**（2026-08-28）：插件框架 `gt_core/plugin/`（加载契约测试 + api_version 守卫 + disabled 标记）；RPC 补 `write_back.run`、`extract.run`→ExtractResult、`plugins.list`→PluginInfo；RPGMV 插件 detect/extract/write_back（字节区间替换，ADR-0004）+ 占位符保护器；黄金样本三测试（快照/空往返 diff=0/带翻译往返）全绿
- **M2 实测**（2026-08-28 真实 MV False Awakening）：extract 2061 条、locator 含文件名无 ID 碰撞；空翻译往返 data 文件字节 diff=0；带翻译往返 re-extract 一致；RPC 端到端（detect→extract→list）44 条 11ms。真实数据暴露两个修正：① 401 参数内嵌 `\n` → context_json 需 `segments` 按段还原行数；② 数据库文件 locator 必须含文件名（`$[1].name` 跨文件碰撞）
- **M3 完成**（2026-08-28）：serve_stdio asyncio 化（单 writer 帧原子 + async handler）；协议定稿 translate.*/providers.*/glossary.delete + ProgressEvent 扩展 + 新错误码；Provider 层（Mock/OpenAI 兼容三形态解析/Manager）；流水线（batcher/stages/pipeline/runner/tasks）；任务表迁移 002（schema_version 1→2）；RPC 日志 api_key 脱敏；插件契约 v1.1 加性演进（protect/restore + features + api_version 区间）。**149 pytest 全绿、mypy 29 文件、ruff 干净**
- **M3 实测**（2026-08-28 真实 DeepSeek）：M3 产品代码全链路（provider.test 2896ms ok + 真实保护器 + 真实翻译 6/6 校验通过占位符 0 破坏）；Mock 5000 条 <30s（test_providers）。M3 验收项「Mock 5000 条」✅、「真实 API 翻译」✅；「断点续翻 kill -9」集成测试、提示词评测集（Phase 6）未建
- **Spike 4 实测**（2026-08-28 真实 DeepSeek）：**AI 翻译完整链路成功**——提取→protect→json_object→restore→字节替换写回→再提取，12 条真实文本 0 非法JSON/0 占位符破坏/0 字段丢失，译文质量人工审通过，写回再提取一致；吞吐 ~1.1 条/s（单请求串行）。**实测发现：模型跟随 user 输入的 JSON 结构当模板**（少样本学习）→ M3 prompt 须在 system 显式声明响应结构 + 解析防御兼容。详 spike4 README
- **M4 UI 重构完成**（2026-09-03）：主界面收束为编辑器（唯一常驻内容区），翻译/回写/模型 API 走 MenuBar 按钮 → Modal 确认；翻译长任务进度在底部 StatusBar（非模态可继续校对），回写结果在弹窗内报告，导入=唯一阻塞覆盖层。删 ActivityBar + `view`/`View`/`setView`；`writeBackResult` 从 store 移入弹窗本地态（每次打开重置）；翻译任务只在 `status==='running'` 时拦截再次启动（残留 done 不误拦）；「行数不匹配重翻」入口保留在 TranslateModal。前端 gate typecheck/lint/build + vitest 3 全绿，bundle gzip 92KB
- **M4 机翻基线 machine_text**（2026-09-03）：人改译文后无法回未改态/看不到机翻 → 根源是 translation 单字段被人工覆盖。加独立基线列 `machine_text`（最近一次 AI 输出）：AI 落库即记基线；人改只动 translation 基线保留（编辑弹窗「机翻原文」可看 + 一键恢复，edited 归 0）；清空回待译（translation=null）基线一并清掉，状态机放行单次 MACHINE→PENDING（CONFIRMED 仍不可逆）。协议 Entry 加字段 → `pnpm protocol`；migration 004（schema 3→4 自动 backfill）。核心 pytest/ruff/mypy + 前端 typecheck/lint/build 全绿
- **M4 模型 API 面板两轮重做 + 连通性修复**（2026-09-03）：`providers.test` 语义定为 **TCP/TLS 握手 ping**（`asyncio.open_connection` + happy_eyeballs，零 HTTP 往返、4s 上限）——旧实现先发真实生成（structured→json_object 降级双轮推理 → 误报 ~5s）、后 GET /models（等服务端 TTFB → 中转实测 ~9s），都把服务端慢误当连接慢。实测 api.deepseek.com 146ms 即通；**key/余额(401/402/403)改由「拉模型列表」`list_models` 那步暴露**（ping 不验 key）。UI 分两步：① 测试连接（ping 秒回「已连接 · xx ms」）→ ② 自动异步拉模型（独立 spinner + 琥珀错误提示 + 重试）。模型下拉换**自绘可滚动 combobox**（WebView2 datalist 无法滚动，用户实测选不了）。预设 chips/key 显隐/卡片删除（providers.remove）。全部 gate 绿（core pytest/ruff/mypy + 前端 typecheck/lint/build/vitest）
- sidecar 打包：PyInstaller onefile → `binaries/gt-core-x86_64-pc-windows-msvc.exe`；Tauri dev 下进程名为 `gt-core.exe`
- **CI 被 GitHub 计费锁定**（2026-08-28）：所有 job runner_id=0（未分配 runner）；本地用 `make ci` 跑全套质量门
- **CI 已无效化**（2026-08-28）：workflow 触发改为仅 `workflow_dispatch`（手动 Run workflow 即启用），push/PR 不再自动跑

## 环境注意（Windows 实测）

- **Tauri v2 新插件必须加 capabilities 权限**（`src-tauri/capabilities/default.json`），否则运行时 `not allowed`：
  `dialog` 加 `dialog:allow-open`；sidecar spawn 加 `shell:allow-spawn`（缺了 sidecar 起不来、握手失败）。
  这是「好多 bug」最常见的来源——不是逻辑错，是权限配置遗漏。加完 `cargo build` 或 `tauri dev` 验证。
- **改 Python 核心后必须 `make sidecar` 重打包**：`tauri dev` 跑的是 `binaries/gt-core-*.exe`（PyInstaller onefile），
  不是源码——不重打包则后端改动全不生效（踩过：状态机放行/连通性 ping 改了但 app 里仍是旧行为）。
  前端 vite 热更即时，Python 侧需手动重打包 + 重启 app/gt-core
- **uv 已装**在 `C:\Users\20819\.local\bin` 与 `~/bin`（新终端 PATH 可用；当前会话若 `uv` 不在 PATH 用 `python -m uv`）
- PyPI/registry 走代理可用；git bash 里进程名查用 `tasklist | grep -i`，杀用 `taskkill //F //IM`
- Python 侧必须 `reconfigure(encoding="utf-8", newline="\n")`，否则 Windows cp936/CRLF 破坏协议帧
- 控制台中文输出是 gbk，脚本 print 避免 `✓/✗` 等非 gbk 字符（会 UnicodeEncodeError）

## 工程纪律（路线图）

- Conventional Commits；单 PR <400 行 diff
- 协议只加不改不删；破坏性变更升 api_version + 迁移层
- 黄金样本三类测试是发布闸门（M2 起）
- 核心模块覆盖率 ≥80%（M1 起测）

## 待办

1. **CI 启用**：已改为仅 `workflow_dispatch`（.github/workflows/ci.yml）——GitHub 页面手动 Run workflow 即启用；计费解锁后如需自动触发再把 push/pull_request 加回（runner_id=0 证据在 m0.md）
2. **M4 打磨**：主结构已定稿——**编辑器=主界面，翻译/回写=按钮→弹窗→确认**（进度进底部状态栏、回写结果在弹窗内、导入=唯一阻塞覆盖层），已删 ActivityBar/View 体系（2026-09-03，见 `docs/ai/context/m4.md`）。待做：双栏原文对照/术语表、行内 diff、键盘批量确认、会话内「重开上次项目」、正式 `tauri dev` 全流程手动验收
3. **M3 遗留**：提示词评测集（Phase 6，100 条黄金样本+人工译文）；断点续翻 kill -9 集成测试；few-shot 上下文注入接入（fill_speaker_and_few_shot 已实现未调用）；resume 不重启已停止协程（需重新 start）；translate_cache 表启用（cache_key 组装）
4. **M2 遗留**：加密游戏（.rpgmvp/.rpgmvo）解密 + 写回策略待做
