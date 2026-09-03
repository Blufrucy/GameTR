"""MockProvider：确定性伪翻译（测试/演示/CI，路线图 3.2）。

关键性质：
- **确定性**：同 source → 同 translation（可复现的黄金样本/断点续翻测试）
- **保留占位符**：translation 原样保留 ⟦n⟧ 哨兵（流水线 Validator 占位符比对必须通过）
- **async 整批**：无 HTTP 开销，5000 条 <30s 验收轻松达标（M3 验收项）
- needs_api_key=False：前端不显示密钥输入框，供演示/CI
"""

from __future__ import annotations

import asyncio

from gt_core.providers.base import TranslateItem, TranslateResult


class MockProvider:
    provider_id = "mock"
    display_name = "Mock Provider（确定性伪翻译）"
    models = ["mock-v1"]
    needs_api_key = False
    supports_structured = False
    base_url: str | None = None

    async def translate_batch(
        self, batch: list[TranslateItem], *, model: str | None = None,
        api_key: str | None = None, glossary: str | None = None,
        few_shot: list[tuple[str, str]] | None = None,
        speaker: str | None = None,
    ) -> list[TranslateResult]:
        # 确定性：前缀标记 + 原文（占位符 ⟦n⟧ 原样保留）
        # 轻量 sleep 模拟处理延迟（可配），默认 0 保证验收吞吐
        return [
            TranslateResult(
                id=item.id,
                translation=f"【译】{item.text}",
                tokens_in=max(1, len(item.text)),
                tokens_out=max(1, len(item.text)),
            )
            for item in batch
        ]

    async def test(self, *, model: str | None = None,
                   api_key: str | None = None) -> tuple[bool, float, str]:
        await asyncio.sleep(0)  # 保持 async 签名一致性
        return True, 0.0, "mock ok"

    async def list_models(self, api_key: str | None = None) -> list[str]:
        return ["mock-v1"]
