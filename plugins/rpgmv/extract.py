"""RPGMV 文本提取器：数据库文件 + 事件指令（路线图 2.2）。

locator 用 JSON Pointer（`$.events[1].pages[0].list[4].parameters[0]`，ADR-0003 不透明）。
每个条目 context_json 存 `{"file_path": 相对游戏根, "char_ranges": [[s,e],...]}`（ADR-0003 写回锚点）。

指令码（真实 MV 工程 2026-08-28 核验）：
- 101 显示文字（params[0]=头像名，非文本）→ 后续连续 401 为正文，拼接成段落（多区间）
- 102 显示选项（params[0]=选项数组）→ 每选项独立成条
- 402 选项分支文本（params[0]）
- 405 滚动文字（params[0]）
- 108/408 注释（params[0]，默认跳过，include_comments 开关）
"""

from __future__ import annotations

import json
from typing import Any

from .ranges import _ptr, locate_strings

# 数据库文件 → 可提取文本字段（按路线图 2.2 清单）
_TEXT_FIELDS: dict[str, list[str]] = {
    "Actors.json": ["name", "nickname", "profile", "note"],
    "Items.json": ["name", "description", "note"],
    "Weapons.json": ["name", "description", "note"],
    "Armors.json": ["name", "description", "note"],
    "Enemies.json": ["name", "note"],
    "Skills.json": ["name", "description", "message1", "message2", "note"],
    "States.json": ["name", "note", "message1", "message2"],
    "MapInfos.json": ["name", "note"],
}


def _entry(pointer: str, source: str, rel_path: str,
           ranges: list[list[int]], segments: list[str] | None = None,
           *, order: int = 0, speaker: str | None = None) -> dict[str, Any]:
    """一条待翻译条目：locator + source + 写回锚点。

    locator = "{文件basename}::{JSON Pointer}"：语义位置必须含文件名——
    数据库文件（Items/Weapons/Actors...）的 root 都是数组，不同文件的 `$[1].name`
    指向不同文本；locator 不限定文件会 ID 碰撞（entry_id 用 locator），落库互相覆盖。
    文件用 basename（MV/MZ 的 data 文件同名，跨版本稳定）。

    context_json = {"file_path", "char_ranges", "segments", "order", "speaker"?}：
    - char_ranges：每个字符串字面量的字节区间（写回锚点，ADR-0003）
    - segments：每区间的原文（401 参数可能内嵌 \\n，write_back 按段还原行数，
      不能靠 source.split("\\n")——那会把段内换行和段间换行混在一起）
    - order：文件内序号（ContextBatcher 排序用——"前一条已确认译文 few-shot"）
    - speaker：说话人（101 指令头像名；M3 翻译上下文，可选）
    """
    ctx: dict[str, Any] = {
        "file_path": rel_path, "char_ranges": ranges,
        "segments": segments if segments is not None else [source],
        "order": order,
    }
    if speaker:
        ctx["speaker"] = speaker
    locator = f"{rel_path.split('/')[-1]}::{pointer}"
    return {
        "locator": locator,
        "source": source,
        "context_json": json.dumps(ctx, ensure_ascii=False),
        "warnings_json": None,
    }


def _add_single(pointer: str, value: Any, rel_path: str,
                loc: dict, entries: list[dict], *, speaker: str | None = None) -> None:
    """单区间条目（数据库字段 / 单行文本）；空字符串跳过。"""
    if not isinstance(value, str) or not value.strip():
        return
    hit = loc.get(pointer)
    if hit is None:
        return  # 防御：解析出的 pointer 不在 locate_strings 结果里
    _, s, e = hit
    entries.append(_entry(pointer, value, rel_path, [[s, e]], order=len(entries),
                          speaker=speaker))


# ---------- 数据库文件 ----------

def extract_data_file(filename: str, text: str, rel_path: str) -> list[dict]:
    """数据库 JSON（数组，如 Actors/Items/.../MapInfos）→ 条目列表。"""
    doc = json.loads(text)
    loc = locate_strings(text)
    entries: list[dict] = []
    fields = _TEXT_FIELDS.get(filename)
    if not isinstance(doc, list) or not fields:
        return entries
    for i, obj in enumerate(doc):
        if not isinstance(obj, dict):
            continue
        for f in fields:
            _add_single(_ptr([i, f]), obj.get(f), rel_path, loc, entries)
    return entries


def extract_system(text: str, rel_path: str) -> list[dict]:
    """System.json → 条目：gameTitle + currencyUnit + terms 下全部字符串。"""
    doc = json.loads(text)
    loc = locate_strings(text)
    entries: list[dict] = []
    if not isinstance(doc, dict):
        return entries
    for f in ("gameTitle", "currencyUnit"):
        _add_single(f"$.{f}", doc.get(f), rel_path, loc, entries)
    _walk_strings(doc.get("terms") or {}, "$.terms", rel_path, loc, entries)
    return entries


def _walk_strings(node: Any, prefix: str, rel_path: str,
                  loc: dict, entries: list[dict]) -> None:
    """递归收集 terms 下所有非空字符串（数组/字典任意嵌套）。"""
    if isinstance(node, dict):
        for k, v in node.items():
            _walk_strings(v, f"{prefix}.{k}", rel_path, loc, entries)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_strings(v, f"{prefix}[{i}]", rel_path, loc, entries)
    elif isinstance(node, str):
        _add_single(prefix, node, rel_path, loc, entries)


# ---------- 事件指令 ----------

def extract_events(text: str, rel_path: str,
                   include_comments: bool = False) -> list[dict]:
    """Map*.json / CommonEvents.json → 条目列表。

    Map*：doc.events[]（含 null 占位）→ pages[].list[]。
    CommonEvents：doc 是数组 → 每元素 .list[]。
    """
    doc = json.loads(text)
    loc = locate_strings(text)
    entries: list[dict] = []
    if isinstance(doc, list):  # CommonEvents.json
        for i, ev in enumerate(doc):
            if isinstance(ev, dict) and ev.get("list"):
                _walk_commands(ev["list"], [i, "list"], rel_path, loc, entries, include_comments)
    elif isinstance(doc, dict):  # Map*.json
        for i, ev in enumerate(doc.get("events", [])):
            if not isinstance(ev, dict):
                continue
            for pi, page in enumerate(ev.get("pages", [])):
                if isinstance(page, dict) and page.get("list"):
                    _walk_commands(page["list"], ["events", i, "pages", pi, "list"],
                                   rel_path, loc, entries, include_comments)
    return entries


def _walk_commands(commands: list[dict], prefix: list[str | int], rel_path: str,
                   loc: dict, entries: list[dict], include_comments: bool) -> None:
    n = len(commands)
    i = 0
    while i < n:
        cmd = commands[i]
        code = cmd.get("code")
        params = cmd.get("parameters") or []
        if code == 101:
            # 消息块：收集后续连续 401，拼接为段落（每 401 一个区间）。
            # 说话人 = 101 的 params[0]（头像名），作 M3 翻译上下文
            speaker = params[0] if params and isinstance(params[0], str) else None
            j = i + 1
            block: list[int] = []
            while j < n and commands[j].get("code") == 401:
                block.append(j)
                j += 1
            if block:
                _add_message_block(block, prefix, rel_path, loc, entries, speaker=speaker)
            i = j
        elif code == 102:
            choices = params[0] if params and isinstance(params[0], list) else []
            for k, choice in enumerate(choices):
                _add_single(_ptr(prefix + [i]) + f".parameters[0][{k}]",
                            choice, rel_path, loc, entries)
            i += 1
        elif code == 402:
            if params:
                _add_single(_ptr(prefix + [i]) + ".parameters[0]", params[0],
                            rel_path, loc, entries)
            i += 1
        elif code == 405:
            if params:
                _add_single(_ptr(prefix + [i]) + ".parameters[0]", params[0],
                            rel_path, loc, entries)
            i += 1
        elif code in (108, 408) and include_comments:
            if params:
                _add_single(_ptr(prefix + [i]) + ".parameters[0]", params[0],
                            rel_path, loc, entries)
            i += 1
        elif code == 401:
            # 孤立 401（无 101 开头，防御性）：单独成条
            _add_single(_ptr(prefix + [i]) + ".parameters[0]",
                        params[0] if params else "", rel_path, loc, entries)
            i += 1
        else:
            i += 1


def _add_message_block(block_idxs: list[int], prefix: list[str | int], rel_path: str,
                       loc: dict, entries: list[dict], *, speaker: str | None = None) -> None:
    """连续 401 拼成一条 Entry：source 用 \\n 连接，char_ranges 收集每个 401 的区间。"""
    lines: list[str] = []
    ranges: list[list[int]] = []
    first_ptr: str | None = None
    for j in block_idxs:
        ptr = _ptr(prefix + [j]) + ".parameters[0]"
        hit = loc.get(ptr)
        if hit is None:
            continue
        val, s, e = hit
        lines.append(val)
        ranges.append([s, e])
        if first_ptr is None:
            first_ptr = ptr
    if not lines or first_ptr is None or not any(ln.strip() for ln in lines):
        return
    entries.append(_entry(first_ptr, "\n".join(lines), rel_path, ranges, segments=lines,
                          order=len(entries), speaker=speaker))
