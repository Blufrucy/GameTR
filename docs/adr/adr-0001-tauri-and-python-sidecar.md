# ADR-0001：为何 Tauri + Python Sidecar

- 状态：已接受
- 日期：2026-08-27
- 相关：ADR-0002（进程间协议）、ADR-0003（locator）

## 背景（Context）

GameTR 是一个桌面游戏翻译器：核心工作流是"提取游戏文本 → AI 翻译 → 人工双栏校对 → 回写游戏"。产品硬性约束：

- **M4 验收**要求双栏表格编辑器流畅编辑 5 万行条目、帧率 >50 —— 只有 Web 技术栈（TanStack Table + 虚拟滚动）能轻松达标
- **M5 验收**要求安装包 <100MB、绿色 zip 开箱即用 —— 排除了 Electron 这种动辄 150MB+ 的方案
- 核心逻辑重度依赖 Python 生态：AI Provider 调用、文本处理、未来的 UnRpyC/UnityPy 等解包工具几乎只有 Python 实现
- 需要跨平台（Win 为主、macOS/Linux 可能）、离线优先、单体应用

## 决策（Decision）

**Tauri v2（Rust 壳 + 系统 WebView 前端）+ Python 核心进程（gt-core）作为 sidecar**，两进程之间用 stdio 通信（见 ADR-0002）。

- Rust 壳负责：窗口、系统集成、sidecar 生命周期（spawn/心跳/kill/重启）、文件对话框
- Web 前端负责：双栏编辑器、翻译控制台、向导 —— 全部通过 RPC 调核心
- Python 核心负责：引擎探测/提取/回写、项目存储、翻译流水线、Provider

## 备选方案与拒绝理由

| 方案 | 拒绝理由 |
| :--- | :--- |
| Electron | 包体积 >150MB 违反 M5（<100MB）；内存占用高；WebView 多套维护 |
| 纯 Python GUI（Tkinter/PyQt/PySide） | 5 万行表格的虚拟滚动与交互体验做不到；UI 打磨成本高 |
| 全 Rust 实现核心 | 解包/文本/AI 生态远不如 Python；RPGMV/Ren'Py 解析都要重写 |
| Rust 内嵌 Python 解释器 | GIL/解释器边界、打包复杂度高；生态隔离做不好，不如进程边界干净 |

## 后果（Consequences）

- 两进程、两语言 → 必须有严格协议层（ADR-0002），RPC 全量日志是排障生命线
- Python 打包体积是 M5 硬指标（core <40MB），sidecar 必须 PyInstaller 单文件（M0 已用 onefile 验证）
- Tauri 官方支持 externalBin sidecar，打包链路（bundle.externalBin）已验证
- 已验证（M0 Spike 1）：Tauri 启动 spawn sidecar、500ms 重试握手、**杀掉 core 进程 GUI 自动重启恢复**

> 经验证：PyInstaller onefile 的 sidecar 在 Windows 上表现为 2 个进程（父 bootloader + 子 Python），进程管理需按父进程识别。
