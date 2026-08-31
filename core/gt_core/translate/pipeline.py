"""翻译流水线（路线图 3.1/3.3）：逐批 翻译→校验→还原→落库。

Pipe-And-Filter 的编排层，跑在后台任务（translate.start create_task）：
  ContextBatcher(make_batches) → Protector(protect_all) → Provider.translate_batch
  → Validator(validate_result, 重试 1 次) → Restorer(restore_all) → Persister(upsert_translations)

- 每批一个事务（repo.upsert_translations），CONFIRMED 在 SQL 层原子跳过
- **并发翻译**：批间 asyncio.Semaphore(_CONCURRENCY) 并发调 Provider（httpx 让出事件循环），
  真实游戏 2061 条/60 文件吞吐瓶颈在 API 串行调用；并发提升吞吐、缩短总时长、
  减少限流概率（用户「40 条后翻译出错」= 频繁串行调用触发 429）
- 批边界检查取消（should_cancel），把取消留给上游任务管理，不传播进单批
- 进度通知回调（notify(done, total, message)），任务管理负责发 ProgressEvent
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from typing import Any

from gt_core.providers.base import TranslateItem, TranslationProvider
from gt_core.rpc.models import Entry, EntryStatus, GlossaryEntry
from gt_core.translate.batcher import Batch, make_batches
from gt_core.translate.stages import (
    ProtectedItem,
    get_protector_fns,
    protect_all,
    validate_result,
)

# 校验失败重试次数（路线图 3.1：重试 1 次仍失败→标 warning 保留结果）
_MAX_RETRY = 1
# 并发 API 请求数：2（实测 3 并发触发 DeepSeek 限流→空响应→任务 error；串行 5 次稳定。
# 并发 2 兼顾吞吐（~2x）与稳定性）
_CONCURRENCY = 2

Notify = Callable[[int, int, str | None], None]
CancelCheck = Callable[[], bool]


def _has_translatable_text(protected: str) -> bool:
    """保护后是否还有可翻译文本（去除占位符哨兵后非空）。

    纯插件标签/控制符（如 `<CTB After Speed: 100%>`、`\\pop[0]`）保护后整条是 ⟦⟧，
    无可译内容——AI 只能返回原文，被漏译检测拒绝后永远待译（实测坑：oriontest 21 条待译
    大多是这类标签）。这类条目应**直接保留原文落库**，不调 AI。
    """
    return bool(re.sub(r"⟦\d+⟧", "", protected).strip())


def format_glossary(entries: list[GlossaryEntry]) -> str:
    """GlossaryInjector：术语表 → system prompt 段落（term = translation 每行）。"""
    return "\n".join(f"{g.term} = {g.translation}" for g in entries)


async def _call_and_validate(
    provider: TranslationProvider,
    items: list[ProtectedItem],
    *,
    model: str | None, api_key: str | None, glossary: str | None,
    restore: Callable[[str, list[str]], str], has_ph: Callable[[str], bool],
) -> tuple[dict[str, str], list[ProtectedItem], list[str], int, int]:
    """调 provider 一次，逐条校验+还原。返回 ({id: 还原译文}, 失败条目, 失败警告, tokens_in, tokens_out)。"""
    batch = [TranslateItem(id=i.id, text=i.protected) for i in items]
    results = await provider.translate_batch(
        batch, model=model, api_key=api_key, glossary=glossary
    )
    by_id = {r.id: r for r in results}
    tokens_in = sum(r.tokens_in for r in results)
    tokens_out = sum(r.tokens_out for r in results)
    ok: dict[str, str] = {}
    failed: list[ProtectedItem] = []
    warnings: list[str] = []
    for item in items:
        r = by_id.get(item.id)
        if r is None:
            failed.append(item)
            warnings.append(f"{item.id}: 响应缺失条目")
            continue
        valid, warn = validate_result(item.protected, r.translation, has_ph)
        if not valid:
            failed.append(item)
            warnings.append(f"{item.id}: {warn}")
            continue
        ok[item.id] = restore(r.translation, item.tokens)
    return ok, failed, warnings, tokens_in, tokens_out


def _build_entries(batch: Batch, restored: dict[str, str]) -> list[Entry]:
    """还原译文 → Entry（status=MACHINE，供 Persister 落库）。"""
    now = time.time()
    out: list[Entry] = []
    for e in batch.entries:
        tr = restored.get(e.id)
        if tr is None:
            continue
        out.append(Entry(
            id=e.id, source=e.source, translation=tr,
            status=EntryStatus.MACHINE, locator=e.locator,
            context_json=e.context_json, warnings_json=None, updated_at=now,
        ))
    return out


async def translate_entries(
    *,
    project: Any,
    entries: list[Entry],
    provider: TranslationProvider,
    model: str | None,
    api_key: str | None,
    glossary: str | None = None,
    protector: Any = None,
    notify: Notify | None = None,
    should_cancel: CancelCheck | None = None,
    overwrite_confirmed: bool = False,
    on_usage: Callable[[int, int], None] | None = None,
) -> tuple[int, list[str]]:
    """逐批翻译。返回 (翻译成功条数, 失败警告列表)。不抛错（失败记 warning）。

    on_usage(tokens_in, tokens_out)：累计上报用量（runner 落 translate_usage 供 stats）。
    """
    p, r, h = get_protector_fns(protector)
    cancel = should_cancel or (lambda: False)
    total = len(entries)
    done = 0
    translated = 0
    tokens_in = tokens_out = 0
    final_warnings: list[str] = []
    batches = make_batches(entries)

    # 并发翻译：批间 Semaphore 并发调 Provider（httpx await 让出事件循环，单线程安全；
    # sqlite 落库与共享计数在非 await 段串行执行，无竞态）
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def process_batch(batch: Batch) -> None:
        nonlocal done, translated, tokens_in, tokens_out, final_warnings
        if cancel():
            return  # 批边界取消
        try:
            async with sem:
                items = protect_all(batch.entries, p)
                # 分流：纯标签/控制符（保护后无可译文本）直接保留原文，不进 AI
                translatable = [it for it in items if _has_translatable_text(it.protected)]
                non_translatable = [it for it in items if not _has_translatable_text(it.protected)]
                restored: dict[str, str] = {}
                for it in non_translatable:
                    restored[it.id] = r(it.protected, it.tokens)  # = source 原文（标签保留）
                if translatable:
                    r1, failed, _w1, ti, to = await _call_and_validate(
                        provider, translatable, model=model, api_key=api_key, glossary=glossary,
                        restore=r, has_ph=h,
                    )
                    tokens_in += ti
                    tokens_out += to
                    restored.update(r1)
                    if failed and _MAX_RETRY > 0:
                        restored2, failed2, warns2, ti2, to2 = await _call_and_validate(
                            provider, failed, model=model, api_key=api_key, glossary=glossary,
                            restore=r, has_ph=h,
                        )
                        tokens_in += ti2
                        tokens_out += to2
                        restored.update(restored2)
                        final_warnings.extend(warns2)  # 重试后仍失败 → warning
                        # 重试后仍失败（AI 未译/内容不可译如 JS/ID）→ 保留原文落库，
                        # 避免永远待译（游戏安全优先：原文在游戏里总比错译好）
                        for it in failed2:
                            restored[it.id] = r(it.protected, it.tokens)
                            final_warnings.append(
                                f"{it.id}: 保留原文（AI 未译或内容不可译，如插件配置/JS/ID）"
                            )
                if restored:
                    out_entries = _build_entries(batch, restored)
                    # 每批一个事务；返回实际更新数（CONFIRMED 被 SQL 层跳过，不计入）
                    translated += project.repo.upsert_translations(
                        out_entries, overwrite_confirmed=overwrite_confirmed
                    )
        except Exception as exc:  # noqa: BLE001 — 批级容错：单批失败（限流/网络）跳过，不中断任务
            final_warnings.append(
                f"{batch.file_path}: 批次翻译失败已跳过（{type(exc).__name__}: {exc}），重新翻译会再试"
            )
        done += len(batch.entries)
        if notify:
            notify(done, total, f"{translated} 条已翻译")

    await asyncio.gather(*(process_batch(b) for b in batches))
    if on_usage:
        on_usage(tokens_in, tokens_out)
    return translated, final_warnings
