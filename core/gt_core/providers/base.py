"""Provider 层（路线图 3.2）：翻译提供方抽象。

协议极小化（KISS，勿扩）：
- translate_batch：一批已 protect 的文本 → 译文列表（批量是吞吐关键，逐条调用成本高）
- test：连通性自检（providers.test）

引擎无关（M6 Ren'Py 复用同一 Provider）；重试/限速抽到 shared transport
（openai_compat），Provider 本身只管「发请求 → 解析 → 报用量」。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TranslateItem:
    """一条待翻译文本（text 已由占位符保护器 protect，含 ⟦n⟧ 哨兵）。"""

    id: str
    text: str


@dataclass(frozen=True)
class TranslateResult:
    """一条翻译结果（translation 保留 ⟦n⟧，流水线后续 restore）。"""

    id: str
    translation: str
    tokens_in: int = 0
    tokens_out: int = 0


class TranslationProvider(Protocol):
    """翻译提供方契约（鸭子类型，不强制继承）。"""

    provider_id: str
    display_name: str
    models: list[str]
    needs_api_key: bool
    supports_structured: bool  # 是否支持 response_format（json_object 及以上）
    base_url: str | None

    async def translate_batch(
        self, batch: list[TranslateItem], *, model: str | None = None,
        api_key: str | None = None, glossary: str | None = None,
    ) -> list[TranslateResult]:
        """翻译一批（并发控制/重试/限速在实现内部或 transport 层）。

        glossary：格式化术语表段落（GlossaryInjector 产出），注入 system prompt。
        """
        ...

    async def test(self, *, model: str | None = None,
                   api_key: str | None = None) -> tuple[bool, float, str]:
        """连通性自检：返回 (ok, latency_ms, message)。"""
        ...

    async def list_models(self, api_key: str | None = None) -> list[str]:
        """获取模型列表（GET /models，OpenAI 兼容端点，UI「获取模型」按钮）。"""
        ...
