#!/usr/bin/env python
"""Spike 2：RPG Maker MV 数据文件往返保真实验（路线图 1.4）。

核心问题：游戏数据 JSON 的写回格式——只有找到能 100% 字节还原原格式的
json.dumps 参数组合，回写才不会破坏原文件格式（缩进/转义/key顺序/中文转义）。

结论写入 ADR-0004。

用法：
  # 1) 生成最小样本工程
  python make_sample.py --out sample_game

  # 2) 空往返：找出能零差异还原原字节的 dumps 参数组合
  python roundtrip.py --file sample_game/www/data/Map001.json

  # 3) 带修改往返：改一条文本 → 用找到的参数写回 → 字节 diff 验证
  python roundtrip.py --file sample_game/www/data/Map001.json \\
      --edit '$.events[0].pages[0].list[1].parameters[0]' 'こんにちは、翻訳済み勇者！' --diff

  # 4) 对整个工程跑一遍（检测所有 data 文件）
  python roundtrip.py --dir sample_game/www/data
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
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


# ---------- 参数空间搜索 ----------

def _dumps_params():
    """穷举 json.dumps 的关键参数组合（缩进/ASCII转义/key顺序/分隔符/尾换行）。"""
    for indent, ensure_ascii, sort_keys, compact_sep, trailing_nl in itertools.product(
        (1, 2, 3, 4),
        (False, True),
        (False, True),
        (False, True),
        (False, True),
    ):
        sep = (",", ":") if compact_sep else None  # None = 默认 (', ', ': ')
        yield {
            "indent": indent,
            "ensure_ascii": ensure_ascii,
            "sort_keys": sort_keys,
            "separators": sep,
            "trailing_newline": trailing_nl,
        }


def serialize(data: Any, params: dict) -> bytes:
    text = json.dumps(data, ensure_ascii=params["ensure_ascii"], indent=params["indent"],
                      sort_keys=params["sort_keys"], separators=params["separators"])
    if params["trailing_newline"]:
        text += "\n"
    return text.encode("utf-8")


def find_zero_diff_params(original: bytes, data: Any) -> list[dict]:
    """返回所有能字节还原原文件的参数组合（可能多个；取第一个为推荐）。"""
    hits = []
    for params in _dumps_params():
        if serialize(data, params) == original:
            hits.append(params)
    return hits


# ---------- 主流程 ----------

def analyze_file(path: Path) -> tuple[Any, list[dict]]:
    original = path.read_bytes()
    data = json.loads(original)
    return data, find_zero_diff_params(original, data)


def main() -> None:
    parser = argparse.ArgumentParser(description="RPGMV JSON 往返保真实验")
    parser.add_argument("--file", help="单个 JSON 文件")
    parser.add_argument("--dir", help="目录（所有 *.json）")
    parser.add_argument("--edit", nargs=2, metavar=("POINTER", "TEXT"),
                        help="修改一条文本后写回（JSON Pointer，如 $.events[0].pages[0].list[1].parameters[0]）")
    parser.add_argument("--diff", action="store_true", help="写回后做字节 diff 验证")
    args = parser.parse_args()

    files = [Path(args.file)] if args.file else sorted(Path(args.dir).glob("*.json")) if args.dir else []

    for path in files:
        original = path.read_bytes()
        data = json.loads(original)
        hits = find_zero_diff_params(original, data)
        print(f"\n== {path} ==")
        print(f"   文件大小: {len(original)} B")
        if hits:
            rec = hits[0]
            print(f"   零差异参数组合: {len(hits)} 个，推荐: "
                  f"indent={rec['indent']} ensure_ascii={rec['ensure_ascii']} "
                  f"sort_keys={rec['sort_keys']} separators={rec['separators']} "
                  f"trailing_newline={rec['trailing_newline']}")
            params = rec
        else:
            print("   未找到零差异组合！（该文件格式超出搜索空间）")
            continue

        if args.edit:
            ptr, text = args.edit
            old = ptr_get(data, ptr)
            ptr_set(data, ptr, text)
            path.write_bytes(serialize(data, params))
            print(f"   已修改 {ptr}: {old!r} -> {text!r}")
            if args.diff:
                again = json.loads(path.read_bytes())
                ptr_set(again, ptr, old)  # 还原文本再比字节
                clean = serialize(again, params) == original
                print(f"   字节 diff（还原后对比）: {'0 差异 OK' if clean else '有差异 FAIL'}")


if __name__ == "__main__":
    main()
