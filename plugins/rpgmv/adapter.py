"""RPGMV 插件适配器（路线图 2.2）：detect / extract / write_back。

插件契约（见 core/gt_core/plugin/manager.py 文档）：
- detect(dir) -> DetectResult 形状 dict
- extract(source_path) -> list[dict]（locator/source/context_json/warnings_json）
- write_back(source_path, output_dir, entries) -> WriteBackResult 形状 dict

write_back 正解 = 字节区间替换（ADR-0004）：拷贝整个游戏到输出目录，
只对被翻译字符串的字面量字节区间做替换，其余字节原样保留 → 格式免疫。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .extract import _TEXT_FIELDS, extract_data_file, extract_events, extract_system
from .protector import has_protected, protect, restore  # noqa: F401 — M3 流水线按契约调用

DATA_DIRS = ("data", "www/data")


def _find_data_dir(root: Path) -> str | None:
    """探测数据目录：MZ 部署版 data/，MV / MZ 编辑器 www/data/。"""
    for rel in DATA_DIRS:
        if (root / rel / "System.json").is_file():
            return rel
    return None


# ---------- detect ----------

def detect(directory: str) -> dict[str, Any]:
    """引擎探测：存在 data/*.json（MZ）或 www/data/*.json（MV）→ rpgmv 0.95。

    空目录/非游戏目录返回 confidence 0（合法 DetectResult，供前端显示失败）。
    """
    root = Path(directory)
    mv_ok = (root / "www" / "data" / "System.json").is_file()
    mz_ok = (root / "data" / "System.json").is_file()
    if not (mv_ok or mz_ok):
        return {"engine_id": "rpgmv", "display_name": "RPG Maker MV/MZ",
                "confidence": 0.0, "version": "", "details": {}}
    # MZ 编辑器工程 data/ 与 www/data/ 都有；部署版只有 data/
    version = "MZ" if mz_ok else "MV"
    confidence = 0.98 if (root / "Game.exe").is_file() else 0.95
    return {
        "engine_id": "rpgmv",
        "display_name": "RPG Maker MV/MZ",
        "confidence": confidence,
        "version": version,
        "details": {"data_dir": "data" if mz_ok else "www/data"},
    }


# ---------- extract ----------

def extract(source_path: str) -> list[dict[str, Any]]:
    """提取全部可翻译文本（数据库文件 + System + 事件脚本）。

    每条 context_json = {"file_path": 相对游戏根, "char_ranges": [[s,e],...]}
    —— 字节区间由 extract 记录，write_back 用（ADR-0003 写回锚点）。
    """
    root = Path(source_path)
    data_dir = _find_data_dir(root)
    if data_dir is None:
        return []  # 非 RPGMV 目录：空提取（detect.run 会先识别，这里防御）
    base = root / data_dir
    entries: list[dict[str, Any]] = []

    for fname in sorted(_TEXT_FIELDS):
        p = base / fname
        if p.is_file():
            text = _read_text(p)
            entries.extend(extract_data_file(fname, text, f"{data_dir}/{fname}"))

    p = base / "System.json"
    if p.is_file():
        entries.extend(extract_system(_read_text(p), f"{data_dir}/System.json"))

    for p in sorted(base.glob("Map*.json")):
        entries.extend(extract_events(_read_text(p), f"{data_dir}/{p.name}"))

    p = base / "CommonEvents.json"
    if p.is_file():
        entries.extend(extract_events(_read_text(p), f"{data_dir}/CommonEvents.json"))

    return entries


def _read_text(p: Path) -> str:
    """按字节读再 UTF-8 解码：不经过 newline 翻译，字节区间（含 CRLF）与写回一致。"""
    return p.read_bytes().decode("utf-8")


# ---------- write_back ----------

def write_back(source_path: str, output_dir: str,
               entries: list[dict[str, Any]]) -> dict[str, Any]:
    """回写：拷贝整个游戏 → 对被翻译条目的字节区间替换 → 写输出目录。

    - 永不写原目录（ADR-0004：写回器输出到用户指定目录 + 拷贝整个游戏）
    - 逐文件按 start 降序替换：高偏移先改，低偏移区间不受影响
    - 行数不匹配 / 译文过长（>原文×1.6）记 warning，不中断
    """
    src = Path(source_path).resolve()
    out = Path(output_dir).resolve()
    if out == src or src in out.parents:
        raise ValueError(f"输出目录不能在源目录内: {output_dir}")

    shutil.copytree(src, out, dirs_exist_ok=True)

    # 按文件分组（context_json.file_path）
    by_file: dict[str, list[tuple[dict[str, Any], list[list[int]]]]] = {}
    for e in entries:
        ctx = json.loads(e.get("context_json") or "{}")
        fp = ctx.get("file_path")
        ranges = ctx.get("char_ranges") or []
        if isinstance(fp, str) and ranges:
            by_file.setdefault(fp, []).append((e, ranges))

    written = 0
    warnings = 0
    msgs: list[str] = []
    for fp, items in by_file.items():
        original = src / fp
        if not original.is_file():
            warnings += len(items)
            msgs.append(f"{fp}: 源文件不存在，跳过 {len(items)} 条")
            continue
        text = _read_text(original)
        swaps: list[tuple[int, int, str]] = []
        for e, ranges in items:
            source = e.get("source") or ""
            translation = e.get("translation")
            if not translation:
                continue
            ctx = json.loads(e.get("context_json") or "{}")
            segments = ctx.get("segments") or [source]
            tgt_lines = translation.split("\n")
            # 每段应得的译文行数 = 该段原文的换行数；总行数不等 → warning
            need_total = sum(len(s.split("\n")) for s in segments)
            if len(tgt_lines) != need_total:
                # 行数不匹配 → 跳过该条（保留原文）：min 逐行写会混原文/译文半译，宁可保原文
                warnings += 1
                msgs.append(
                    f"行数不匹配 {e['locator']}（原文{need_total}行/译文{len(tgt_lines)}行），已保留原文"
                )
                continue
            if len(translation) > len(source) * 1.6:
                warnings += 1
                msgs.append(f"译文过长 {e['locator']}: {len(translation)} > {int(len(source) * 1.6)}")
            # 逐段分配译文行（min 原则：译文行不足/超出时多余段保持原文，记 warning）
            tgt_idx = 0
            wrote = False
            for (s, e2), seg in zip(ranges, segments, strict=True):
                need = len(seg.split("\n"))
                take = min(need, len(tgt_lines) - tgt_idx)
                if take > 0:
                    new_text = "\n".join(tgt_lines[tgt_idx:tgt_idx + take])
                    swaps.append((s, e2, json.dumps(new_text, ensure_ascii=False)))
                    wrote = True
                tgt_idx += take
            if wrote:
                written += 1
        if not swaps:
            continue
        swaps.sort(key=lambda x: x[0], reverse=True)
        for s, e2, lit in swaps:
            text = text[:s] + lit + text[e2:]
        out_path = out / fp
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(text.encode("utf-8"))

    return {
        "output_dir": str(out),
        "written_count": written,
        "warning_count": warnings,
        "message": "; ".join(msgs) if msgs else None,
    }
