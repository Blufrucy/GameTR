# AGENTS.md — GameTR 项目上下文记忆

> 给 AI 协作者的工作记忆。改动/决策/背景优先更新本文件与 `docs/ai/context/`、`docs/adr/`。

## 项目是什么

桌面游戏翻译器：Tauri v2（Rust 壳 + React 前端）+ Python 核心 `gt-core`（sidecar，stdio JSON-RPC）。
目标引擎：RPG Maker MV/MZ（M2）、Ren'Py（M6）。离线优先、AI 翻译 + 人工双栏校对。
总路线图见 `游戏翻译器总路线图.md`（当前处于 **M0**，M0 主体已完成）。

## 架构决策（已定稿，勿改）

- **ADR-0001** Tauri + Python sidecar：Web 前端（5万行表格）、Python 生态（解包/AI）、包体积<100MB
- **ADR-0002** JSON-RPC 2.0 over stdio / NDJSON：stdout 纯净、UTF-8、一行一消息、日志走 stderr+文件
- **ADR-0003** locator 对核心不透明字符串：插件解析，核心只存不解析
- **ADR-0004** RPGMV 回写参数：`json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False)`（已验证）
- **ADR-0005** AI 结构化输出：占位符保护器是硬要求，`response_format` 默认 json_schema（实测待 API Key）

## 代码结构速览

- `core/` — Python 包 `gt-core`（uv workspace member，根 `uv sync` 可编辑安装）
  - `gt_core/rpc/server.py`：NDJSON 帧 + MethodRegistry（装饰器注册）；`__main__.py` CLI（serve/self-test）
  - `gt_core/rpc/models.py`、`plugin_manifest.py`：**协议生成物，禁止手改**，改 `protocol/schema/` 后 `pnpm protocol`
- `apps/desktop/` — Tauri v2 + React
  - `src-tauri/src/lib.rs`：sidecar 生命周期（spawn/握手/心跳重启/kill），`core_ping` command
  - `src/rpc/models.ts`、`plugin-manifest.ts`：协议生成物，禁止手改
- `protocol/schema/` — 单一事实源（common/rpc-methods/plugin-manifest）
- `tests/spikes/` — M0 Spike 实验（spike2_rpgmv 往返、spike3_ai 结构化输出）

## 已验证的事实（不要重复验证）

- core RPC：15 个 pytest 通过；mypy/ruff 干净；`gt-core serve` 管道 ping/shutdown 往返 OK
- **Spike 1 端到端**：Tauri 启动 spawn sidecar → 握手成功；**杀 gt-core 进程 → 自动重启恢复**（日志 `[sidecar] 进程终止…自动重启`）
- **Spike 2**：RPGMV JSON 往返零差异参数 = `indent=2, ensure_ascii=False, sort_keys=False, 无尾换行`
- **Spike 3**：占位符保护/还原/破坏检测逻辑自洽（48 条样本）；脚本就绪，实测需 API Key
- sidecar 打包：PyInstaller onefile → `binaries/gt-core-x86_64-pc-windows-msvc.exe`；Tauri dev 下进程名为 `gt-core.exe`

## 环境注意（Windows 实测）

- **uv 已装**在 `C:\Users\20819\.local\bin` 与 `~/bin`（新终端 PATH 可用；当前会话若 `uv` 不在 PATH 用 `python -m uv`）
- PyPI/registry 走代理可用；git bash 里进程名查用 `tasklist | grep -i`，杀用 `taskkill //F //IM`
- Python 侧必须 `reconfigure(encoding="utf-8", newline="\n")`，否则 Windows cp936/CRLF 破坏协议帧
- 控制台中文输出是 gbk，脚本 print 避免 `✓/✗` 等非 gbk 字符（会 UnicodeEncodeError）

## 工程纪律（路线图）

- Conventional Commits；单 PR <400 行 diff
- 协议只加不改不删；破坏性变更升 api_version + 迁移层
- 黄金样本三类测试是发布闸门（M2 起）
- 核心模块覆盖率 ≥80%（M1 起测）

## 待办（M0 之后）

1. **Spike 3 实测**：配 API Key 跑 `run_batch.py`，把 metrics 填回 ADR-0005
2. **Spike 2 真实工程验证**：拿真实 MV 工程跑 `roundtrip.py --dir`，回填 ADR-0004
3. 首次 git commit 整个 M0（当前全部未提交）
4. 从 M1 开始：IR/存储/状态机/RPC 全量方法
