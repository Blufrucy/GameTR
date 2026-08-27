#!/usr/bin/env python
"""生成 core 的 pydantic 模型（common + plugin-manifest）+ 确定性后处理。

被 `pnpm protocol`（generate.mjs）与 CI 复用，保证产物一致：
- 枚举语义名：datamodel-codegen 在 jsonschema 模式产出 integer_N，这里重命名
- 删除纯 definitions 容器产生的无用根模型 Model

用法：uv run python protocol/scripts/gen_python_models.py
（脚本内部自动探测 uv，Windows 下 uv 不在 PATH 时回退 python -m uv）
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
COMMON = ROOT / "protocol" / "schema" / "common.json"
MANIFEST = ROOT / "protocol" / "schema" / "plugin-manifest.json"
OUT = ROOT / "core" / "gt_core" / "rpc" / "models.py"
OUT_MANIFEST = ROOT / "core" / "gt_core" / "plugin_manifest.py"

# 与 common.json 的 EntryStatus 枚举语义一一对应
_ENUM_RENAME = {
    "integer_1": "PENDING",
    "integer_2": "MACHINE",
    "integer_3": "EDITED",
    "integer_4": "CONFIRMED",
}


def _uv_cmd() -> list[str]:
    return ["uv"] if shutil.which("uv") else [sys.executable, "-m", "uv"]


def _codegen(schema: Path, out: Path) -> None:
    subprocess.run(
        [
            *_uv_cmd(),
            "run",
            "datamodel-codegen",
            "--input", str(schema),
            "--input-file-type", "jsonschema",
            "--output", str(out),
            "--output-model-type", "pydantic_v2.BaseModel",
            "--target-python-version", "3.11",
            "--use-annotated",
            "--disable-timestamp",  # 生成物必须幂等（CI 用 git diff 校验一致）
        ],
        cwd=ROOT,
        check=True,
    )


def _postprocess(path: Path) -> None:
    code = path.read_text(encoding="utf-8")
    for old, new in _ENUM_RENAME.items():
        code = code.replace(f"    {old} = ", f"    {new} = ")
    # Windows CRLF 兼容；生成物里的 Model 根类不被任何字段引用
    code = code.replace("\r\nclass Model(RootModel[Any]):\r\n    root: Any\r\n", "\n")
    code = code.replace("\nclass Model(RootModel[Any]):\n    root: Any\n", "\n")
    path.write_text(code, encoding="utf-8")


def main() -> None:
    _codegen(COMMON, OUT)
    _codegen(MANIFEST, OUT_MANIFEST)
    _postprocess(OUT)
    print(f"Python 模型已生成: {OUT.name}, {OUT_MANIFEST.name}")


if __name__ == "__main__":
    main()
