#!/usr/bin/env python
"""Spike 2：RPG Maker 数据文件往返保真实验（路线图 1.4）。

核心问题：游戏数据 JSON 的写回格式——回写译文时如何不破坏原文件字节。
本模块产出两个独立能力，各有明确分工：

1. **字节区间替换**（write_back 正解，M2 插件用它）
   `locate_strings` / `apply_text_swap`：定位每个字符串字面量的字节区间
   [start, end)，只替换该区间，其余字节原样保留 → 格式无关。
   2026-08-28 验证：真实 MV(83)+MZ(17) 共 17580 个字符串 100% 定位精确、
   替换后只有目标值变、其余字节逐字不变。见 ADR-0003「写回锚点」。

2. **重序列化校验器**（黄金样本"空翻译 diff=0"，不用于写回）
   `detect_style` / `serialize_rpgm`：复刻引擎 serializer 以校验「不改任何文本时
   重写 == 原文」。曾一度当作 write_back 实现，但发现格式怪癖不可穷尽（MV 空
   events 展开 / CRLF / 插件 indent=2），故 write_back 改用字节替换，本能力
   降级为校验器。见 ADR-0004。

用法：
  python make_sample.py --out sample_game
  python roundtrip.py --dir <真实工程>/www/data                 # 空往返校验（重序列化 diff=0）
  python roundtrip.py --file .../Map001.json \
      --edit '$.events[1].name' '新文本' --diff                 # 字节替换改一条并验证
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------- JSON Pointer（RFC 6901 简化 + 路线图 locator 风格 $.a[0].b） ----------

_TOKEN_RE = re.compile(r"[^.[\]]+|\[\d+\]")


def _parse_pointer(pointer: str) -> list[str | int]:
    s = pointer.removeprefix("$")
    toks: list[str | int] = []
    for m in _TOKEN_RE.findall(s):
        toks.append(int(m[1:-1]) if m.startswith("[") else m)
    return toks


def ptr_get(doc: Any, pointer: str) -> Any:
    cur = doc
    for token in _parse_pointer(pointer):
        cur = cur[int(token)] if isinstance(cur, list) else cur[token]
    return cur


def ptr_set(doc: Any, pointer: str, value: Any) -> None:
    parts = _parse_pointer(pointer)
    cur = doc
    for token in parts[:-1]:
        cur = cur[int(token)] if isinstance(cur, list) else cur[token]
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def _ptr(path: list[str | int]) -> str:
    s = "$"
    for t in path:
        s += f".{t}" if isinstance(t, str) else f"[{t}]"
    return s


# ---------- 字节区间替换（write_back 核心，见 ADR-0003 写回锚点） ----------

def locate_strings(text: str) -> dict[str, tuple[str, int, int]]:
    """带字节区间的 JSON 解析：返回 {path: (字符串值, 字面量start, 字面量end)}。

    区间含引号，可直接切片替换（`text[start:end]` == `json.dumps(值)`）。
    字符偏移而非字节偏移（text 是 str），写盘时整体 UTF-8 编码，天然对齐。
    """
    dec = json.JSONDecoder()
    n = len(text)
    out: dict[str, tuple[str, int, int]] = {}

    def skip(i: int) -> int:
        while i < n and text[i] in " \t\r\n":
            i += 1
        return i

    def walk(i: int, path: list[str | int]) -> int:
        i = skip(i)
        c = text[i]
        if c == '"':
            start = i
            val, i = dec.raw_decode(text, i)
            out[_ptr(path)] = (val, start, i)
            return i
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
    return out


def apply_text_swap(text: str, pointer: str, new_value: str) -> str:
    """字节替换：把 pointer 指向的字符串字面量替换为 new_value，其余字节不变。

    write_back 的正解：不重序列化、不碰格式（CRLF/缩进/插件怪癖天然保留），
    只把被翻译字符串的字面量区间换成新值（新值按 ensure_ascii=False 转义）。
    """
    loc = locate_strings(text)
    if pointer not in loc:
        raise KeyError(f"locator 不在文件中或不是字符串值: {pointer}")
    _, start, end = loc[pointer]
    return text[:start] + json.dumps(new_value, ensure_ascii=False) + text[end:]


# ---------- 原文件风格探测 + 重序列化（黄金样本校验器，见 ADR-0004） ----------

@dataclass(frozen=True)
class Style:
    """一次重序列化所需的原文件格式特征（从原文件探测，勿硬编码）。"""
    newline: str = "\n"                      # \n 或 \r\n（Map001 实测是 CRLF）
    expand_paths: frozenset[str] = frozenset()  # 空数组被展开的位置（如 $.events）
    indent: int | None = None                # 插件文件（Doodads）用 indent=2


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
    """重序列化（仅用于黄金样本"空翻译 diff=0"校验，不用于 write_back）。"""
    style = style or Style()
    if style.indent is not None:
        return json.dumps(v, ensure_ascii=False, indent=style.indent).replace("\n", style.newline)
    return _ser(v, [], style)


# ---------- 主流程 ----------

def analyze_file(path: Path) -> tuple[Any, bool]:
    """返回 (解析后的数据, 是否字节级可复刻)。"""
    text = path.read_bytes().decode("utf-8")
    data = json.loads(text)
    return data, serialize_rpgm(data, detect_style(text)) == text


def main() -> None:
    parser = argparse.ArgumentParser(description="RPG Maker JSON 往返保真实验")
    parser.add_argument("--file", help="单个 JSON 文件")
    parser.add_argument("--dir", help="目录（所有 *.json）")
    parser.add_argument("--edit", nargs=2, metavar=("POINTER", "TEXT"),
                        help="字节替换改一条文本（JSON Pointer，如 $.events[1].name）")
    parser.add_argument("--diff", action="store_true", help="写回后做字节 diff 验证")
    args = parser.parse_args()

    files = [Path(args.file)] if args.file else sorted(Path(args.dir).glob("*.json")) if args.dir else []

    for path in files:
        original = path.read_bytes().decode("utf-8")
        data = json.loads(original)
        style = detect_style(original)
        ok = serialize_rpgm(data, style) == original
        nl = "CRLF" if style.newline == "\r\n" else "LF"
        print(f"\n== {path} ==")
        print(f"   文件大小: {len(original)} B  风格: {nl}"
              + (" indent=2" if style.indent else f" 展开={sorted(style.expand_paths)}" if style.expand_paths else ""))
        print(f"   字节级复刻: {'OK（serialize_rpgm 零差异）' if ok else 'FAIL（超出算法范围）'}")

        if args.edit:
            ptr, text = args.edit
            old = ptr_get(data, ptr)
            # write_back 正解：字节替换，不重序列化
            swapped = apply_text_swap(original, ptr, text)
            path.write_bytes(swapped.encode("utf-8"))
            print(f"   已字节替换 {ptr}: {old!r} -> {text!r}")
            if args.diff:
                # 还原：再字节替换回原值，逐字节对比
                restored = apply_text_swap(swapped, ptr, old)
                path.write_bytes(restored.encode("utf-8"))
                print(f"   字节 diff（还原后对比）: {'0 差异 OK' if restored == original else '有差异 FAIL'}")


if __name__ == "__main__":
    main()
