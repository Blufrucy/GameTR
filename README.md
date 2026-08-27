# GameTR

一款桌面游戏翻译器：提取游戏文本 → AI 翻译 → 双栏人工校对 → 回写游戏。
当前支持 RPG Maker MV/MZ（规划中），Ren'Py 在路线图 M6。

架构：**Tauri v2 桌面壳（Rust + React）⇄ stdio JSON-RPC/NDJSON ⇄ Python 核心 `gt-core`**。
设计决策见 [docs/adr/](docs/adr/)（ADR-0001~0005）。

```
┌──────────────────┐  JSON-RPC over stdio   ┌─────────────────────┐
│ Tauri 壳 (Rust)   │ ◄────────────────────► │ gt-core (Python)     │
│ 窗口 / 双栏编辑器  │   一行一个消息(NDJSON)   │ 提取/翻译/回写/存储  │
│ 前端 (React)      │                         │ 插件(引擎) + Provider │
└──────────────────┘                         └─────────────────────┘
```

## Monorepo 结构

| 目录 | 说明 |
| :--- | :--- |
| `apps/desktop/` | Tauri v2 + React 前端（`src-tauri/` 为 Rust 壳，管理 sidecar 生命周期） |
| `core/` | Python 包 `gt-core`（uv 管理）：stdio JSON-RPC 服务、项目存储、翻译流水线 |
| `protocol/` | JSON Schema 契约（单一事实源）+ 生成脚本，产物进 core 与 desktop，**禁止手改** |
| `plugins/` | 引擎适配插件（RPGMV 等，M2 起） |
| `providers/` | 翻译 Provider（OpenAI 兼容端点等，M3 起） |
| `tests/` | 集成与 Spike 实验（`spikes/spike2_rpgmv/`、`spikes/spike3_ai/`） |
| `docs/adr/` | 架构决策记录 |
| `docs/ai/context/` | AI 协作上下文文档 |

## 开发快速开始

前置：Python 3.11+ 与 [uv](https://docs.astral.sh/uv/)、Node 20+ 与 pnpm、Rust stable、Tauri CLI v2。

```bash
# 核心（Python）
uv sync                                # 安装 core + 全部开发工具（根 .venv）
uv run pytest                          # 跑 core 测试
uv run gt-core serve                   # stdio RPC 模式（手动管道可测）
uv run gt-core self-test               # headless 自检

# 桌面（Tauri + React）
pnpm install
pnpm --filter desktop tauri dev        # 启动 GUI（自动 spawn sidecar 并握手）

# 协议重新生成（改了 protocol/schema/ 之后）
pnpm protocol

# 侧边栏打包（PyInstaller → Tauri externalBin）
uv run python core/scripts/build_sidecar.py
```

> uv 不在 PATH 时用 `python -m uv` 替代（Windows 首次安装后需重开终端）。

## 当前进度

处于 **M0（工程奠基 + 三大风险 Spike）**，M0 主体已完成：

- [x] Monorepo 骨架、pre-commit、CI 三 job、ADR-0001~0005
- [x] protocol 层（schema + 双端生成，CI 校验生成物一致）
- [x] **Spike 1**：Tauri ⇄ Python sidecar 通信（握手、杀进程自动重启）已端到端验证
- [x] **Spike 2**：RPGMV JSON 往返保真参数已定（`indent=2, ensure_ascii=False`），真实工程待补验
- [x] **Spike 3**：AI 结构化输出测试工具就绪，实测待 API Key

完整计划见 [游戏翻译器总路线图.md](游戏翻译器总路线图.md)。

## 工程质量（贯穿全程）

- Conventional Commits；单 PR <400 行 diff
- 核心模块覆盖率 ≥80%；**黄金样本往返测试是发布闸门**
- 协议（RPC 方法/IR 字段）只加不改不删
