# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包 gt-core sidecar（onefile）。

用法：uv run pyinstaller --noconfirm --clean packaging/gt_core.spec
产物：dist/gt-core.exe

为什么 onefile（而非 M5 计划的 onedir）：
Tauri externalBin 需要单个可执行文件（bundler 只拷贝一个文件）。
onedir 的启动速度优势留到 M5 用打包目录方案解决，见 docs/ai/context/m0.md 的 tradeoff。
"""

from pathlib import Path

_root = Path(SPECPATH).resolve().parent  # core/（spec 位于 core/packaging/）

a = Analysis(
    [str(_root / "gt_core" / "__main__.py")],
    pathex=[str(_root)],
    binaries=[],
    # 数据文件随包：引擎插件（_MEIPASS/plugins）+ SQL 迁移（_MEIPASS/gt_core/project/migrations）
    datas=[
        (str(_root.parent / "plugins"), "plugins"),
        (str(_root / "gt_core" / "project" / "migrations"), "gt_core/project/migrations"),
    ],
    # 显式收集 rpc 子模块（__main__ 通过函数引用间接 import，防止 tree-shaking 漏掉）
    hiddenimports=[
        "gt_core.rpc.server",
        "gt_core.rpc.methods",
        "gt_core.rpc.errors",
        "gt_core.providers",
        "gt_core.translate",
    ],
    hookspath=[],
    runtime_hooks=[],
    # 桌面端核心不需要 GUI 框架，显著瘦身
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="gt-core",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # sidecar 无独立控制台窗口，stdio 走管道
    disable_windowed_traceback=False,
)
