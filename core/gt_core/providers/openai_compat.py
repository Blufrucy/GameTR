"""OpenAICompatibleProvider：一套代码通吃 OpenAI/DeepSeek/Qwen/豆包/Ollama（路线图 3.2）。

- base_url 可配（含完整路径前缀）：OpenAI `https://api.openai.com/v1`，DeepSeek `https://api.deepseek.com`
- response_format：**优先 json_schema structured outputs（官方推荐，strict 强制结构）**；
  端点不支持（DeepSeek 等）时自动降级 json_object（旧 JSON mode，模型会跟 user 模板乱改结构）
- 响应解析：三形态兼容（translations/items/顶层数组 + text/translation 字段）
- 重试：429/5xx/传输错误/超时 → 指数退避 3 次（传输层容错，勿写进各 Provider）
- 用量：从响应 usage 提取，返回 tokens_in/out（落 translate_usage 供 stats）

传输用 httpx.AsyncClient（async 不阻塞事件循环；urllib 同步会卡住 asyncio）。

2026-08 对照 context7/OpenAI 官方 API 参考更新：
- max_tokens 已废弃 → 保留（第三方兼容端点 DeepSeek/Qwen/豆包普遍只认它；
  max_completion_tokens 仅 OpenAI 官方新模型需要，翻译场景不触发其差异）
- response_format 官方推荐 json_schema（structured outputs），json_object 是旧方式——
  故先试 json_schema strict，400 降级 json_object
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from gt_core.providers.base import TranslateItem, TranslateResult

# 指数退避重试：429/5xx/传输错误/超时最多重试 MAX_RETRIES 次
_MAX_RETRIES = 3
_BASE_DELAY = 1.0
# 响应解析重试：模型偶发返回空/非 JSON content（DeepSeek 限流实测坑），
# 指数退避重新请求（2s/4s/8s 给限流恢复时间）而非直接失败
_PARSE_RETRY = 3

# structured outputs 的 JSON Schema（strict 子集：顶层 object、全字段 required、无 additionalProperties）
_TRANSLATIONS_SCHEMA: dict[str, Any] = {
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
        },
    },
    "required": ["translations"],
    "additionalProperties": False,
}


class StructuredUnsupportedError(RuntimeError):
    """端点不支持 json_schema structured outputs（400 且 response_format 相关），降级 json_object。"""


class ProviderAuthError(RuntimeError):
    """鉴权/余额错误（401/402/403）：**不重试**（重试无意义），报清晰中文原因。

    API 没钱（402）、key 无效（401）、无权限（403）——用户需要充值/换 key，
    重试只会浪费请求与时间。
    """

    def __init__(self, status_code: int, detail: str = "") -> None:
        self.status_code = status_code
        reason = {
            401: "API key 无效或已过期，请检查「模型 API」设置",
            402: "API 余额不足，请充值后继续翻译",
            403: "无权限访问（可能余额不足或账户受限）",
        }.get(status_code, f"HTTP {status_code}")
        msg = f"{reason}（{status_code}）" + (f": {detail}" if detail else "")
        super().__init__(msg)


class OpenAICompatibleProvider:
    # 类级类型声明（Protocol 契约：base_url 可 None；mock 用 None，本实现为 str）
    base_url: str | None

    def __init__(
        self,
        *,
        provider_id: str = "openai",
        display_name: str = "OpenAI 兼容端点",
        base_url: str = "https://api.openai.com/v1",
        models: list[str] | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.display_name = display_name
        self.base_url = base_url.rstrip("/")
        self.models = models or ["default"]
        self.needs_api_key = True
        self.supports_structured = True  # response_format（json_object 及以上）

    # ---------- 请求 ----------

    def _system_prompt(self, glossary: str | None) -> str:
        system = (
            "你是游戏本地化译者，把文本翻译成简体中文。"
            "要求：符合角色语气，口语自然，术语一致。"
            "文本可能含换行（多行内容），译文必须保持与原文相同的行数——"
            "每行对应一行，不得合并或拆分，换行符必须原样保留。"
        )
        if glossary:
            system += f"\n术语表（必须优先采用）：\n{glossary}"
        system += (
            "\n文本中的 ⟦数字⟧ 是占位符，必须原样保留（数量、顺序、编号都不能变），不得翻译、删除或改动。"
            "响应中的 id 必须与请求中的 id 完全一致，不得修改或重命名。"
            '只输出 JSON：{"translations": [{"id": "n0", "translation": "译文"}]}，不要任何解释。'
        )
        return system

    def _payload(self, batch: list[TranslateItem], model: str,
                 glossary: str | None = None, *, structured: bool = False) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": self._system_prompt(glossary)},
                {"role": "user", "content": json.dumps(
                    {"items": [{"id": i.id, "text": i.text} for i in batch]}, ensure_ascii=False
                )},
            ],
            "max_tokens": 4096,  # 第三方兼容端点通用（max_completion_tokens 仅 OpenAI 新模型，见模块 docstring）
        }
        if structured:
            # json_schema structured outputs：strict 强制精确结构，杜绝模型改结构
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "translations",
                    "strict": True,
                    "schema": _TRANSLATIONS_SCHEMA,
                },
            }
        else:
            body["response_format"] = {"type": "json_object"}
        return body

    async def _post(self, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
        """带指数退避重试的 POST；返回完整响应体。

        端点不支持 json_schema（400 且 response_format 相关）时抛
        StructuredUnsupportedError，由 translate_batch 降级 json_object 重试。
        """
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                # 超时 60s：DeepSeek 限流/慢响应时更快失败重试，避免单批卡 120s×重试=6 分钟
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 400 and payload.get("response_format", {}).get("type") == "json_schema":
                    # 端点不认识 json_schema structured outputs → 降级 json_object
                    raise StructuredUnsupportedError()
                if resp.status_code in (401, 402, 403):
                    # 鉴权/余额：不重试（重试无意义），清晰报错（API 没钱场景）
                    raise ProviderAuthError(resp.status_code)
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < _MAX_RETRIES:
                    await asyncio.sleep(_BASE_DELAY * (2 ** attempt))
                    continue
                resp.raise_for_status()
                body = resp.json()
                assert isinstance(body, dict)
                return body
            except StructuredUnsupportedError:
                raise  # 不重试，交给调用方降级
            except ProviderAuthError:
                raise  # 不重试（余额/鉴权），直接报错
            except (httpx.HTTPStatusError, httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_BASE_DELAY * (2 ** attempt))
        raise RuntimeError(f"Provider 请求失败（重试 {_MAX_RETRIES} 次后）: {last_exc}")

    # ---------- 解析（三形态兼容，spike4 实测） ----------

    @staticmethod
    def _parse_translations(content: str) -> list[dict[str, Any]]:
        data = json.loads(content)
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            for key in ("translations", "items", "data"):
                if isinstance(data.get(key), list):
                    items = data[key]
                    break
            else:
                raise ValueError(f"响应缺翻译数组: keys={list(data)[:5]}")
        else:
            raise ValueError(f"响应结构未知: {type(data).__name__}")
        out: list[dict[str, Any]] = []
        for t in items:
            if not isinstance(t, dict):
                continue
            tid = t.get("id")
            val = t.get("translation")
            if val is None:
                val = t.get("text")  # 模型跟随 user 模板时用 text
            if isinstance(tid, str) and isinstance(val, str):
                out.append({"id": tid, "translation": val})
        return out

    # ---------- 契约实现 ----------

    async def translate_batch(
        self, batch: list[TranslateItem], *, model: str | None = None,
        api_key: str | None = None, glossary: str | None = None,
    ) -> list[TranslateResult]:
        if not batch:
            return []
        if not api_key:
            raise ValueError(f"{self.provider_id} 需要 api_key（环境变量或参数）")
        model = model or self.models[0]
        # 优先 json_schema structured outputs（官方推荐，strict 强制结构）；
        # 端点不支持（DeepSeek 等）时降级 json_object
        body = None
        for structured in (True, False):
            payload = self._payload(batch, model, glossary=glossary, structured=structured)
            try:
                body = await self._post(payload, api_key)
                break
            except StructuredUnsupportedError:
                continue  # 降级 json_object 重试
        assert body is not None

        # 解析 + 重试：模型偶发返回空/非 JSON content（实测坑），重新请求而非直接失败
        last_parse: Exception | None = None
        for _ in range(_PARSE_RETRY + 1):
            content = body["choices"][0]["message"]["content"]
            try:
                parsed = self._parse_translations(content)
                break
            except (json.JSONDecodeError, ValueError) as exc:
                last_parse = exc
                if _ < _PARSE_RETRY:
                    # 空/非 JSON 多为限流（并发触发，实测坑）→ 指数退避给限流恢复时间
                    await asyncio.sleep(2 * (2 ** _))  # 2s, 4s
                    body = await self._post(payload, api_key)
        else:
            raise RuntimeError(f"响应多次非 JSON（{_PARSE_RETRY + 1} 次）: {last_parse}")
        usage = body.get("usage") or {}
        tokens_in = int(usage.get("prompt_tokens", 0))
        tokens_out = int(usage.get("completion_tokens", 0))
        by_id = {t["id"]: t["translation"] for t in parsed}
        # 按请求顺序返回（缺失的条目用原文兜底？不——由流水线 Validator 判定失败）
        results = []
        for item in batch:
            tr = by_id.get(item.id)
            if tr is None:
                raise ValueError(f"响应缺失条目 {item.id}（共 {len(parsed)}/{len(batch)}）")
            results.append(TranslateResult(
                id=item.id, translation=tr, tokens_in=tokens_in, tokens_out=tokens_out
            ))
        return results

    async def test(self, *, model: str | None = None,
                   api_key: str | None = None) -> tuple[bool, float, str]:
        """连通性自检：验证「能拿到翻译」即可，不依赖 id 严格匹配。

        （模型对单条 probe 请求偶发改 id，测试连接目的是连通性而非数据完整性；
        流水线 translate_batch 仍严格按 id 校验，失败重试。）
        """
        if not api_key:
            return False, 0.0, "缺 api_key（环境变量注入）"
        t0 = time.monotonic()
        try:
            use_model = model or (
                self.models[0] if self.models and self.models[0] != "default" else None
            )
            if not use_model:
                # 无有效模型（临时配置未选模型）：拉 /models 用第一个探测
                try:
                    fetched = await self.list_models(api_key=api_key)
                    use_model = fetched[0] if fetched else None
                except Exception:  # noqa: BLE001 — /models 拉不到也继续走 chat 探测
                    use_model = None
            if not use_model:
                ms = (time.monotonic() - t0) * 1000.0
                return False, ms, "未找到可用模型（先获取模型，或检查 API 地址 /models 端点）"
            payload = self._payload(
                [TranslateItem(id="probe", text="こんにちは")], use_model
            )
            body = await self._post(payload, api_key)
            content = body["choices"][0]["message"]["content"]
            parsed = self._parse_translations(content)
            ms = (time.monotonic() - t0) * 1000.0
            if not parsed or not parsed[0].get("translation"):
                return False, ms, f"响应无翻译条目: {content[:120]}"
            return True, ms, "ok"
        except Exception as exc:  # noqa: BLE001 — 自检失败返回信息而非抛
            return False, (time.monotonic() - t0) * 1000.0, f"{type(exc).__name__}: {exc}"

    async def list_models(self, api_key: str | None = None) -> list[str]:
        """获取模型列表：GET {base_url}/models（OpenAI 兼容标准端点）。

        供「添加 API → 获取模型 → 选择模型」流程自动填充模型下拉。
        """
        if not api_key:
            raise ValueError(f"{self.provider_id} 需要 api_key")
        url = f"{self.base_url}/models"
        headers = {"Authorization": f"Bearer {api_key}"}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        assert isinstance(data, dict)
        raw = data.get("data", [])
        return [m["id"] for m in raw if isinstance(m, dict) and m.get("id")]
