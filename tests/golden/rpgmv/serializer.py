"""RPG Maker 数据文件重序列化器（黄金样本校验器，ADR-0004 降级产物）。

从 tests/spikes/spike2_rpgmv/roundtrip.py 迁移（2026-08-28 真实 MZ+MV 双工程
验证的唯一正确实现）。用途两处：
1. make_sample.py 用它生成**真实格式**的样本工程（JS 紧凑 + null 数组展开换行）
2. 黄金样本"空翻译往返 diff=0"用它校验 write_back 输出

⚠️ 仅作生成器/校验器，**不是 write_back 实现**（write_back 用字节区间替换，
见 plugins/rpgmv/ranges.py + ADR-0004）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Style:
    """一次重序列化所需的原文件格式特征（从原文件探测，勿硬编码）。"""
    newline: str = "\n"                      # \n 或 \r\n（Map001 实测是 CRLF）
    expand_paths: frozenset[str] = frozenset()  # 空数组被展开的位置（如 $.events）
    indent: int | None = None                # 插件文件（Doodads）用 indent=2


def _ptr(path: list[Any]) -> str:
    s = "$"
    for t in path:
        s += f".{t}" if isinstance(t, str) else f"[{t}]"
    return s


def _scan_expanded(text: str) -> list[str]:
    """扫描原文件，返回所有'展开数组'（[ 后紧跟换行）的路径。"""
    paths: list[str] = []
    dec = json.JSONDecoder()
    n = len(text)

    def skip(i: int) -> int:
        while i < n and text[i] in " \t\r\n":
            i += 1
        return i

    def walk(i: int, path: list[Any]) -> int:
        i = skip(i)
        c = text[i]
        if c == "{":
            i += 1
            while True:
                i = skip(i)
                if text[i] == "}":
                    return i + 1
                k, i = dec.raw_decode(text, i)
                i = skip(i)
                assert text[i] == ":"
                i += 1
                vi = skip(i)
                if text[vi] == "[":
                    j = vi + 1
                    while j < n and text[j] in " \t":
                        j += 1
                    if j < n and text[j] in "\r\n":
                        paths.append(_ptr(path + [k]))
                i = walk(i, path + [k])
                i = skip(i)
                if i < n and text[i] == ",":
                    i += 1
        elif c == "[":
            i += 1
            idx = 0
            while True:
                i = skip(i)
                if text[i] == "]":
                    return i + 1
                i = walk(i, path + [idx])
                idx += 1
                i = skip(i)
                if i < n and text[i] == ",":
                    i += 1
        else:
            return dec.raw_decode(text, i)[1]
        return i

    walk(0, [])
    return paths


def detect_style(text: str) -> Style:
    """从原文件文本探测重序列化风格：换行符、展开数组位置、是否 indent=2。"""
    nl = "\r\n" if "\r\n" in text else "\n"
    if any(line.startswith(" ") for line in text.split(nl)):
        return Style(newline=nl, indent=2)  # 插件文件（Doodads）整文件降级
    return Style(newline=nl, expand_paths=frozenset(_scan_expanded(text)))


def _scalar(v: Any) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, float):
        return json.dumps(v)
    return str(v)


def _compact(v: Any) -> str:
    """紧凑序列化（等价 JS JSON.stringify，恒内联，无换行）。"""
    if isinstance(v, dict):
        return "{" + ",".join(json.dumps(k) + ":" + _compact(val) for k, val in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ",".join(_compact(e) for e in v) + "]"
    return _scalar(v)


def _expand(v: list, path: list[Any], style: Style) -> str:
    """展开数组：每元素一行；空数组（被探测到展开）→ [\n]。"""
    if not v:
        return "[" + style.newline + "]"
    inner = style.newline.join(
        _ser(e, path + [i], style) + ("," if i < len(v) - 1 else "")
        for i, e in enumerate(v)
    )
    return "[" + style.newline + inner + style.newline + "]"


def _ser(v: Any, path: list[Any], style: Style) -> str:
    if isinstance(v, list):
        if None in v or (not v and _ptr(path) in style.expand_paths):
            return _expand(v, path, style)
        return _compact(v)
    if isinstance(v, dict):
        if not any(
            isinstance(val, list)
            and (None in val or (not val and _ptr(path + [k]) in style.expand_paths))
            for k, val in v.items()
        ):
            return _compact(v)
        blocks: list[str] = []
        cur: list[str] = []
        for k, val in v.items():
            p = path + [k]
            if isinstance(val, list) and (val or (not val and _ptr(p) in style.expand_paths)):
                if cur:
                    blocks.append(",".join(cur))
                    cur = []
                blocks.append(json.dumps(k) + ":" + _ser(val, p, style))
            else:
                cur.append(json.dumps(k) + ":" + _compact(val))
        if cur:
            blocks.append(",".join(cur))
        return "{" + style.newline + ("," + style.newline).join(blocks) + style.newline + "}"
    return _scalar(v)


def serialize_rpgm(v: Any, style: Style | None = None) -> str:
    """重序列化（仅用于黄金样本校验/生成，不用于 write_back）。"""
    style = style or Style()
    if style.indent is not None:
        return json.dumps(v, ensure_ascii=False, indent=style.indent).replace("\n", style.newline)
    return _ser(v, [], style)
