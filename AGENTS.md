# AGENTS.md — GameTR 项目上下文记忆

> 给 AI 协作者的工作记忆。改动/决策/背景优先更新本文件与 `docs/ai/context/`、`docs/adr/`。

## 项目是什么

桌面游戏翻译器：Tauri v2（Rust 壳 + React 前端）+ Python 核心 `gt-core`（sidecar，stdio JSON-RPC）。
目标引擎：RPG Maker MV/MZ（M2）、Ren'Py（M6）。离线优先、AI 翻译 + 人工双栏校对。
总路线图见 `游戏翻译器总路线图.md`（当前处于 **M1**，核心内核已实现，详见 `docs/ai/context/m1.md`）。

## 架构决策（已定稿，勿改）

- **ADR-0001** Tauri + Python sidecar：Web 前端（5万行表格）、Python 生态（解包/AI）、包体积<100MB
- **ADR-0002** JSON-RPC 2.0 over stdio / NDJSON：stdout 纯净、UTF-8、一行一消息、日志走 stderr+文件
- **ADR-0003** locator 对核心不透明字符串：插件解析，核心只存不解析
- **ADR-0004** RPGMV 回写参数：`json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False)`（已验证）
- **ADR-0005** AI 结构化输出：占位符保护器是硬要求，`response_format` 默认 json_schema（实测待 API Key）
- **ADR-0006** SQLite 存储：单连接+WAL、迁移目录序号连续、FTS5 trigram+LIKE降级、单条严格状态机 vs 批量跳过CONFIRMED（M1）

## 代码结构速览

- `core/` — Python 包 `gt-core`（uv workspace member，根 `uv sync` 可编辑安装）
  - `gt_core/rpc/server.py`：NDJSON 帧 + MethodRegistry（装饰器注册，支持 schema 参数校验）；`__main__.py` CLI（serve/self-test）
  - `gt_core/rpc/models.py`、`plugin_manifest.py`：**协议生成物，禁止手改**，改 `protocol/schema/` 后 `pnpm protocol`
  - `gt_core/ir.py`：稳定 ID `sha1(engine_id+locator+source[:256])[:16]`；locator 契约
  - `gt_core/pipeline.py`：项目/条目状态机（非法迁移抛异常）+ 批量守卫（CONFIRMED 不可批量改）
  - `gt_core/project/`：db.py（WAL 连接）/ migrator.py（版本连续迁移）/ repo.py（DAO）/ project.py（门面）
  - `gt_core/rpc/params.py`：方法参数 pydantic 模型（extra=forbid）
- `apps/desktop/` — Tauri v2 + React
  - `src-tauri/src/lib.rs`：sidecar 生命周期（spawn/握手/心跳重启/kill），`core_ping` command
  - `src/rpc/models.ts`、`plugin-manifest.ts`：协议生成物，禁止手改
- `protocol/schema/` — 单一事实源（common/rpc-methods/plugin-manifest）
- `tests/spikes/` — M0 Spike 实验；`tests/e2e/m1_flow.py` — M1 验收脚本（10 万条全流程）

## 已验证的事实（不要重复验证）

- core RPC + M1：**105 个 pytest 通过**，核心覆盖率 **93%**；mypy/ruff 干净
- **M1 全流程验收**：`python tests/e2e/m1_flow.py`（10 万条插入 ~3.5s，12 项 PASS，含 RPC 日志回放）
- **M1 性能**：5 万条分页 <50ms、FTS/LIKE 搜索 <100ms（test_perf）
- **M1 review 修复**：4 维度审查 14 个确认缺陷已修复（IR 碰撞/迁移原子性/未来版本守卫/通知应答/null 语义/IN 分块/FTS 重索引 等），回归在 test_m1_fixes.py
- **Spike 1 端到端**：Tauri 启动 spawn sidecar → 握手成功；**杀 gt-core 进程 → 自动重启恢复**
- **Spike 2**：RPGMV JSON 往返零差异参数 = `indent=2, ensure_ascii=False, sort_keys=False, 无尾换行`
- **Spike 3**：占位符保护/还原/破坏检测逻辑自洽（48 条样本）；实测待 API Key
- sidecar 打包：PyInstaller onefile → `binaries/gt-core-x86_64-pc-windows-msvc.exe`；Tauri dev 下进程名为 `gt-core.exe`
- **CI 被 GitHub 计费锁定**（2026-08-28）：所有 job runner_id=0（未分配 runner）；本地用 `make ci` 跑全套质量门
- **CI 已无效化**（2026-08-28）：workflow 触发改为仅 `workflow_dispatch`（手动 Run workflow 即启用），push/PR 不再自动跑

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

## 待办

1. **Spike 3 实测**：配 API Key 跑 `run_batch.py`，把 metrics 填回 ADR-0005
2. **Spike 2 真实工程验证**：拿真实 MV 工程跑 `roundtrip.py --dir`，回填 ADR-0004
3. **CI 启用**：已改为仅 `workflow_dispatch`（.github/workflows/ci.yml）——GitHub 页面手动 Run workflow 即启用；计费解锁后如需自动触发再把 push/pull_request 加回（runner_id=0 证据在 m0.md）
4. **M2**：插件框架 + RPGMV 适配器（detect/extract/write_back、占位符保护器、黄金样本三测试）
5. **M1 遗留**：`entries.list` 的 file_path 过滤依赖 context_json（extract 写入），M2 落地时验证
