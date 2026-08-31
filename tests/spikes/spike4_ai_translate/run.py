#!/usr/bin/env python
"""Spike 4：AI 翻译真实性验证（真实 API + 真实游戏文本 + 占位符保护器 + 写回）。

回答"往返流程跑通了，但 AI 翻译是否真的能成功翻译"：
链路 = 提取 → 占位符保护 → DeepSeek(json_object) → 还原占位符 → 写回 → 再提取。

判定指标：
- 非法 JSON 率（json_object 应 0）
- 占位符破坏率（数量+顺序+编号必须一致）
- 字段丢失率（缺 id/translation）
- 吞吐（条/s）
- 译文质量 = 人工审（脚本打印原文/译文对照）

用法：python run.py [--max-entries 10]
配置从 ../spike3_ai/.env 读（OPENAI_API_KEY/BASE_URL/MODEL）。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent.parent
# Windows 控制台默认 gbk，占位符 ⟦⟧ 打不出来 → 强制 UTF-8（AGENTS.md 环境坑）
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_SPH = re.compile(r"⟦(\d+)⟧")


def _load_module(name: str, path: Path):
    """动态加载无包模块（spike 跨目录依赖用，不污染 sys.path）。

    先注册 sys.modules 再 exec，让模块内的绝对导入（如 make_sample 的
    `from serializer import ...`）能找到兄弟模块。
    """
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# M2 占位符保护器 + 黄金样本生成器（动态加载，保持自包含）
_protector = _load_module("protector", REPO / "plugins" / "rpgmv" / "protector.py")
_serializer = _load_module("serializer", REPO / "tests" / "golden" / "rpgmv" / "serializer.py")
_make_sample = _load_module("make_sample", REPO / "tests" / "golden" / "rpgmv" / "make_sample.py")

from gt_core.plugin import PluginManager  # noqa: E402 — 核心包已安装，正常导入


def ph_sequence(text: str) -> list[str]:
    return _SPH.findall(text)


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env_file = HERE.parent / "spike3_ai" / ".env"
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def call_model(payload: dict, env: dict, timeout: int = 120) -> tuple[str | None, str | None, float]:
    url = f"{env['OPENAI_BASE_URL'].rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {env['OPENAI_API_KEY']}", "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers=headers, method="POST")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body["choices"][0]["message"]["content"], None, time.monotonic() - t0
    except (urllib.error.HTTPError, urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
        return None, f"{type(exc).__name__}: {exc}", time.monotonic() - t0


_SYSTEM = (
    "你是 RPG 游戏本地化译者，把日文/英文游戏文本翻译成简体中文。"
    "要求：符合游戏角色语气，术语一致，口语自然。"
    "文本中的 ⟦数字⟧ 是占位符，必须原样保留（数量、顺序、编号都不能变），不得翻译、删除或改动。"
    "只输出 JSON：{\"translations\": [{\"id\": \"n0\", \"translation\": \"译文\"}]}，不要任何解释。"
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-entries", type=int, default=10)
    args = parser.parse_args()
    env = load_env()
    assert env.get("OPENAI_API_KEY", "").startswith("sk-"), "spike3_ai/.env 缺 OPENAI_API_KEY"

    # 1) 提取黄金样本真实文本（程序化生成的真实格式样本）
    game = Path(tempfile.mkdtemp()) / "game"
    _make_sample.generate(game)
    entries = PluginManager([str(REPO / "plugins")]).get_entry("rpgmv").extract(str(game))

    # 选含占位符的优先，再补数据库字段（代表性）
    with_ph = [e for e in entries if "\\" in e["source"]]
    rest = [e for e in entries if e not in with_ph]
    picks = (with_ph + rest)[: args.max_entries]
    print(f"选中 {len(picks)} 条（占位符 {len(with_ph)} 条优先）")

    # 2) 保护 + 构造 batch
    batch: list[dict] = []
    for i, e in enumerate(picks):
        protected, tokens = _protector.protect(e["source"])
        batch.append({"id": f"n{i}", "text": protected, "tokens": tokens, "entry": e})

    payload = {
        "model": env["OPENAI_MODEL"],
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": json.dumps(
                {"items": [{"id": b["id"], "text": b["text"]} for b in batch]},
                ensure_ascii=False,
            )},
        ],
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},
    }

    # 3) 调用
    print(f"调用 {env['OPENAI_MODEL']}（{env['OPENAI_BASE_URL']}）…")
    content, err, dt = call_model(payload, env)
    if err:
        print(f"API 调用失败: {err}")
        sys.exit(1)
    print(f"响应耗时 {dt:.1f}s")

    # 4) 解析 + 校验。实测发现（2026-08-28）：模型会跟随 user 输入的 JSON 结构
    #    当模板回显（少样本学习），故兼容 translations / items / 顶层数组三种形态。
    try:
        data = json.loads(content)
        if isinstance(data, list):
            translations = data
        elif isinstance(data, dict):
            for key in ("translations", "items", "data"):
                if isinstance(data.get(key), list):
                    translations = data[key]
                    break
            else:
                raise ValueError(f"响应缺翻译数组: keys={list(data)[:5]}")
        else:
            raise ValueError(f"响应结构未知: {type(data).__name__}")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        print(f"响应非法 JSON: {exc}\n{content[:500]}")
        sys.exit(1)

    by_id = {}
    for t in translations:
        if not isinstance(t, dict):
            continue
        tid = t.get("id")
        # 字段兼容 translation / text（模型跟随 user 模板时会用 text）
        val = t.get("translation")
        if val is None:
            val = t.get("text")
        by_id[tid] = val

    ph_broken = 0
    field_loss = 0
    print("\n===== 译文对照 =====")
    for b in batch:
        tr = by_id.get(b["id"])
        if not isinstance(tr, str):
            field_loss += 1
            print(f"[丢失] {b['id']}")
            continue
        if ph_sequence(tr) != ph_sequence(b["text"]):
            ph_broken += 1
            print(f"[占位符破坏] {b['id']}: {tr!r}")
            continue
        final = _protector.restore(tr, b["tokens"])
        assert not _protector.has_protected(final)
        src = b["entry"]["source"].replace("\n", " / ")
        print(f"  原文: {src!r}")
        print(f"  译文: {final.replace(chr(10), ' / ')!r}")

    n = len(batch)
    print(f"\n===== 指标（{n} 条） =====")
    print(f"  非法JSON: 0 / {n}")
    print(f"  字段丢失: {field_loss} / {n}")
    print(f"  占位符破坏: {ph_broken} / {n}")
    print(f"  吞吐: {n / dt:.1f} 条/s（{n} 条 / {dt:.1f}s）")

    # 5) 写回 → 再提取（证明 AI 译文进得了游戏文件）
    if ph_broken == 0 and field_loss == 0:
        for b in batch:
            tr = by_id.get(b["id"])
            b["entry"]["translation"] = _protector.restore(tr, b["tokens"]) if isinstance(tr, str) \
                else b["entry"]["source"]
        adapter = PluginManager([str(REPO / "plugins")]).get_entry("rpgmv")
        out = Path(tempfile.mkdtemp()) / "out"
        res = adapter.write_back(str(game), str(out), [b["entry"] for b in batch])
        re_entries = {e["locator"]: e["source"] for e in adapter.extract(str(out))}
        ok = all(re_entries.get(b["entry"]["locator"]) == b["entry"]["translation"]
                 for b in batch if b["entry"].get("translation"))
        print(f"\n  写回验证: {res['written_count']} 条写入, warning={res['warning_count']}, "
              f"再提取一致={ok}")


if __name__ == "__main__":
    main()
