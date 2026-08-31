#!/usr/bin/env python
"""Spike 3：AI 结构化输出可靠性测试（路线图 1.5）。

统计三项核心指标，决定是否需要 response_format / 重试策略（结论写 ADR-0005）：
- 非法JSON率：解析失败 / 总请求数
- 占位符破坏率：译文占位符序列不一致 / 有译文条目数
- 字段丢失率：缺 id 或缺 translation / 收到条目数

只依赖标准库（urllib），无第三方依赖。

配置（环境变量，.env 同字段，环境变量优先）：
  OPENAI_BASE_URL          默认 https://api.openai.com/v1（OpenAI 兼容端点）
  OPENAI_API_KEY           必填（--dry-run 除外；也接受 DEEPSEEK_API_KEY）
  OPENAI_MODEL             默认 gpt-4o-mini
  OPENAI_RESPONSE_FORMAT   可选 json_schema | json_object | 空（不请求结构化）

实测注意（2026-08 DeepSeek 文档）：
  - 模型名 deepseek-chat/deepseek-reasoner 已停用，改用 deepseek-v4-flash（非思考）/ deepseek-v4-pro（思考）
  - base_url https://api.deepseek.com，无需 /v1
  - DeepSeek 只支持 response_format json_object，不支持 json_schema strict

用法：
  python run_batch.py --limit 100                          # 默认配置跑 100 条
  python run_batch.py --limit 20 --response-format json_schema
  python run_batch.py --dry-run                            # 无密钥自检流程
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# 占位符语法（RPGMV 插件自带，路线图 M2 2.2）：\C[n] \I[n] \N[n] \V[n] \G \{ \. \^ \| \!
_PH_RE = re.compile(
    r"\\C\[\d+\]|\\I\[\d+\]|\\N\[\d+\]|\\V\[\d+\]"
    r"|\\G|\\\{|\\\.|\\\^|\\\||\\!"
)
_MARK_RE = re.compile(r"⟦\d+⟧")

HERE = Path(__file__).resolve().parent
SAMPLE_FILE = HERE / "sample_texts.json"


def protect(text: str) -> tuple[str, list[str]]:
    """把占位符替换为 ⟦n⟧，返回 (受保护文本, 原占位符列表)。"""
    phs: list[str] = []

    def _repl(m: re.Match[str]) -> str:
        phs.append(m.group(0))
        return f"⟦{len(phs) - 1}⟧"

    return _PH_RE.sub(_repl, text), phs


def ph_sequence(text: str) -> list[str]:
    return _MARK_RE.findall(text)


def check_placeholders(src_protected: str, translation: str) -> bool:
    """占位符序列必须完全一致（数量+顺序+编号）。"""
    return ph_sequence(translation) == ph_sequence(src_protected)


# ---------- 模型调用 ----------

def build_payload(model: str, system: str, user: str, resp_format: str | None) -> dict:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        # json_object 模式下官方要求设 max_tokens 防截断（截断=非法 JSON）
        "max_tokens": 4096,
    }
    if resp_format == "json_schema":
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "translations",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "translations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "translation": {"type": "string"},
                                },
                                "required": ["id", "translation"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["translations"],
                    "additionalProperties": False,
                },
            },
        }
    elif resp_format == "json_object":
        payload["response_format"] = {"type": "json_object"}
    return payload


def call_model(payload: dict, base_url: str, api_key: str, timeout: int) -> tuple[str | None, str | None, float]:
    """返回 (响应文本或None, 错误信息或None, 耗时秒)。"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = f"{base_url.rstrip('/')}/chat/completions"
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        return content, None, time.monotonic() - started
    except (urllib.error.HTTPError, urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
        return None, f"{type(exc).__name__}: {exc}", time.monotonic() - started


# ---------- 指标统计 ----------

class Metrics:
    def __init__(self) -> None:
        self.requests = 0
        self.parse_failures = 0
        self.entries_received = 0
        self.entries_missing_fields = 0
        self.entries_checked_ph = 0
        self.ph_destroyed = 0
        self.latencies: list[float] = []
        self.failures: list[dict] = []
        self.total_tokens = 0

    def summary(self) -> dict:
        n = max(self.requests, 1)
        return {
            "requests": self.requests,
            "invalid_json_rate": self.parse_failures / n,
            "placeholder_destruction_rate": self.ph_destroyed / max(self.entries_checked_ph, 1),
            "field_loss_rate": self.entries_missing_fields / max(self.entries_received, 1),
            "avg_latency_ms": sum(self.latencies) / n * 1000 if self.latencies else 0.0,
            "total_entries_received": self.entries_received,
            "failure_count": len(self.failures),
        }


def extract_translations(content: str, requested_ids: set[str]) -> tuple[dict[str, str], list[dict]]:
    """解析模型返回的 JSON，返回 (id->translation, 问题列表)。兼容 {translations:[...]} 与裸数组两种形态。"""
    problems: list[dict] = []
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        raise
    arr = data["translations"] if isinstance(data, dict) and isinstance(data.get("translations"), list) else (
        data if isinstance(data, list) else None
    )
    out: dict[str, str] = {}
    if arr is None:
        return out, [{"kind": "unexpected_shape", "content": content[:200]}]
    for item in arr:
        if not isinstance(item, dict) or "id" not in item or "translation" not in item:
            problems.append({"kind": "missing_field", "item": item})
            continue
        out[str(item["id"])] = item["translation"]
    return out, problems


# ---------- 主流程 ----------

def _load_env_file(path: Path) -> None:
    """把 .env 加载进 os.environ（仅设置未已存在的变量）。

    Windows 下临时 export 麻烦，放 .env 一劳永逸；真实环境变量优先于 .env。
    key 敏感信息只放 .env（已 gitignore），不进 git。
    """
    if not path.exists():
        return
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip():
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 结构化输出可靠性测试（Spike 3）")
    parser.add_argument("--limit", type=int, default=100, help="测试条目数上限")
    parser.add_argument("--batch", type=int, default=20, help="每请求条目数")
    parser.add_argument("--response-format", choices=["json_schema", "json_object", "none"], default=None)
    parser.add_argument("--dry-run", action="store_true", help="不调用 API，打印流程与示例请求")
    parser.add_argument("--out", default=str(HERE / "report.json"), help="报告输出路径")
    parser.add_argument("--timeout", type=int, default=120, help="单请求超时（秒）")
    args = parser.parse_args()

    _load_env_file(HERE / ".env")  # 先加载 .env，让环境变量优先
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    resp_format = args.response_format or os.environ.get("OPENAI_RESPONSE_FORMAT") or None

    if args.dry_run:
        print("[dry-run] 将执行以下流程（不调用 API）：")
        print(f"  样本: {SAMPLE_FILE.name} 条目总数={len(json.loads(SAMPLE_FILE.read_text('utf-8')))}")
        print(f"  目标: {model} @ {base_url}  响应格式: {resp_format or '（不指定）'}")
        print(f"  批大小: {args.batch}  上限: {args.limit}")
        sample_payload = build_payload(model, "system…", "user…", resp_format)
        print("  示例请求体: " + json.dumps(sample_payload, ensure_ascii=False)[:160] + "…")
        print("[dry-run] OK")
        return

    if not api_key:
        sys.exit("缺少 OPENAI_API_KEY（或使用 --dry-run 自检流程）")

    texts = json.loads(SAMPLE_FILE.read_text("utf-8"))
    texts = texts[: args.limit]
    metrics = Metrics()

    for start in range(0, len(texts), args.batch):
        batch = texts[start : start + args.batch]
        # 1. 占位符保护
        entries = []
        for item in batch:
            protected, _ = protect(item["text"])
            entries.append({"id": item["id"], "speaker": item.get("speaker", ""), "text": protected})
        user_msg = json.dumps(entries, ensure_ascii=False)

        system = (
            "你是游戏本地化译者，把日文游戏文本翻译成简体中文。"
            "保留 ⟦0⟧⟦1⟧ 这类占位符：数量、顺序、编号都不得改变。"
            "只输出 JSON 数组：[{\"id\":\"...\",\"translation\":\"...\"}]，不要多余文字。"
        )
        payload = build_payload(model, system, user_msg, resp_format)
        metrics.requests += 1
        content, err, latency = call_model(payload, base_url, api_key, args.timeout)
        metrics.latencies.append(latency)
        if err is not None:
            metrics.parse_failures += 1
            metrics.failures.append({"kind": "request_error", "batch_ids": [e["id"] for e in entries], "error": err})
            print(f"  [批 {start // args.batch}] 请求失败: {err}")
            continue
        if content is None:
            metrics.parse_failures += 1
            metrics.failures.append({"kind": "empty_response", "batch_ids": [e["id"] for e in entries]})
            continue

        try:
            translations, problems = extract_translations(content, {e["id"] for e in entries})
        except json.JSONDecodeError as exc:
            metrics.parse_failures += 1
            metrics.failures.append({"kind": "invalid_json", "batch_ids": [e["id"] for e in entries], "error": str(exc), "content": content[:500]})
            print(f"  [批 {start // args.batch}] 非法 JSON: {exc}")
            continue

        metrics.entries_received += len(translations)
        metrics.entries_missing_fields += len(problems)
        for p in problems:
            metrics.failures.append({"kind": p["kind"], "batch_ids": [e["id"] for e in entries], "item": p.get("item")})

        # 2. 占位符破坏检测
        for item in entries:
            translation = translations.get(item["id"])
            if translation is None:
                continue
            metrics.entries_checked_ph += 1
            if not check_placeholders(item["text"], translation):
                metrics.ph_destroyed += 1
                metrics.failures.append({
                    "kind": "placeholder_destroyed",
                    "id": item["id"],
                    "src": item["text"],
                    "translation": translation,
                })
        print(f"  [批 {start // args.batch}] 完成: {len(translations)}/{len(batch)} 条")

    # 3. 报告
    summary = metrics.summary()
    report = {"config": {"model": model, "base_url": base_url, "response_format": resp_format, "limit": args.limit, "batch": args.batch}, "metrics": summary, "failures": metrics.failures}
    out = Path(args.out)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 报告 ===")
    print(f"  请求数:        {summary['requests']}")
    print(f"  非法JSON率:    {summary['invalid_json_rate']:.2%}")
    print(f"  占位符破坏率:  {summary['placeholder_destruction_rate']:.2%}")
    print(f"  字段丢失率:    {summary['field_loss_rate']:.2%}")
    print(f"  平均耗时:      {summary['avg_latency_ms']:.0f} ms")
    print(f"  失败样本:      {len(metrics.failures)} 条 -> {out}")
    print("\n  判定参考（路线图 1.5 预设阈值，结论入 ADR-0005）：")
    print("    - 非法JSON率 >2%       -> 必须开 response_format")
    print("    - 占位符破坏率 >1%     -> 流水线必须带占位符保护器+还原器")
    print("    - 字段丢失率 >1%       -> 需要重试/校验策略")


if __name__ == "__main__":
    main()
