"""字节区间定位与替换（RPGMV 插件域，write_back 正解，见 ADR-0003/0004）。

从 tests/spikes/spike2_rpgmv/roundtrip.py 正式化迁移（2026-08-28 真实 MV+MZ
双工程 17580 字符串验证无损）。语义：
- locate_strings：带字节区间的 JSON 解析，返回 {JSON Pointer: (值, 字面量start, 字面量end)}
- apply_text_swap：只替换目标字符串的字面量区间，其余字节原样保留 → 格式免疫
  （CRLF / 缩进 / 插件怪癖 / 空数组展开都不碰，翻译器对任何格式变体免疫）

区间是字符偏移（str 索引，含引号），UTF-8 编码后自然对齐字节。
"""

from __future__ import annotations

import json
import re
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
    """按 JSON Pointer 取值（extract 校验用；locator 语义归插件，见 ADR-0003）。"""
    cur = doc
    for token in _parse_pointer(pointer):
        cur = cur[int(token)] if isinstance(cur, list) else cur[token]
    return cur


def _ptr(path: list[str | int]) -> str:
    s = "$"
    for t in path:
        s += f".{t}" if isinstance(t, str) else f"[{t}]"
    return s


# ---------- 字节区间定位 ----------

def locate_strings(text: str) -> dict[str, tuple[str, int, int]]:
    """带字节区间的 JSON 解析：返回 {pointer: (字符串值, 字面量start, 字面量end)}。

    区间含引号（`text[start:end]` == `json.dumps(值)`），可直接切片替换。
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

    write_back 正解：不重序列化、不碰格式，只把字面量区间换成新值。
    """
    loc = locate_strings(text)
    if pointer not in loc:
        raise KeyError(f"locator 不在文件中或不是字符串值: {pointer}")
    _, start, end = loc[pointer]
    return text[:start] + json.dumps(new_value, ensure_ascii=False) + text[end:]
