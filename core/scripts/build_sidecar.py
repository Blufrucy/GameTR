#!/usr/bin/env python
"""构建 gt-core sidecar 并放入 Tauri externalBin 目录。

流程：
1. PyInstaller 打包 core（onefile）→ core/dist/gt-core.exe
2. 复制到 apps/desktop/src-tauri/binaries/gt-core-<target-triple>.exe
   （Tauri externalBin 约定：文件名带 target triple，见 tauri.conf.json bundle.externalBin）

用法：uv run python core/scripts/build_sidecar.py
（必须用 venv 的 python，PyInstaller 在根 dev 依赖里）
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CORE = ROOT / "core"
SPEC = CORE / "packaging" / "gt_core.spec"
DIST_EXE = CORE / "dist" / "gt-core.exe"
BIN_DIR = ROOT / "apps" / "desktop" / "src-tauri" / "binaries"


def main() -> None:
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC)],
        cwd=CORE,
        check=True,
    )
    if not DIST_EXE.exists():
        sys.exit(f"打包产物不存在: {DIST_EXE}")

    triple = subprocess.run(
        ["rustc", "--print", "host-tuple"], capture_output=True, text=True, check=True
    ).stdout.strip()
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    target = BIN_DIR / f"gt-core-{triple}.exe"
    shutil.copy2(DIST_EXE, target)
    print(f"sidecar 已就位: {target}")


if __name__ == "__main__":
    main()
