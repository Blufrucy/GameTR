"""翻译流水线（路线图 3.1/3.3）：逐批 翻译→校验→还原→落库。

Pipe-And-Filter 的编排层，跑在后台任务（translate.start create_task）：
  ContextBatcher(make_batches) → Protector(protect_all) → Provider.translate_batch
  → Validator(validate_result, 重试 1 次) → Restorer(restore_all) → Persister(upsert_translations)

- 每批一个事务（repo.upsert_translations），CONFIRMED 在 SQL 层原子跳过
- **并发翻译**：批间 asyncio.Semaphore(_CONCURRENCY) 并发调 Provider（httpx 让出事件循环），
  吞吐瓶颈在 API 串行；429/空响应已排队化（Retry-After 退避）可自恢复
- 批边界检查取消（should_cancel），把取消留给上游任务管理，不传播进单批
- 进度通知回调（notify(done, total, message)），任务管理负责发 ProgressEvent

提速/提质（2026-09-03，M4）：见 fill_speaker_and_few_shot / 同源去重 / 翻译缓存。
- 准确度：few-shot 注入（同文件已确认译文）+ 说话人 → Provider prompt；术语表 glossary 照旧
- 速度：同源去重（游戏重复台词只请求一次）+ 持久翻译缓存（translate_cache，同条件重跑
  0 成本）+ 并发 4（可环境变量下调）
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gt_core.project.repo import Repo

from gt_core.providers.base import TranslateItem, TranslationProvider
from gt_core.rpc.models import Entry, EntryStatus, GlossaryEntry
from gt_core.translate.batcher import Batch, fill_speaker_and_few_shot, make_batches
from gt_core.translate.stages import (
    ProtectedItem,
    get_protector_fns,
    protect_all,
    validate_result,
)

# 校验失败重试次数（路线图 3.1：重试 1 次仍失败→标 warning 保留结果）
_MAX_RETRY = 1
# 并发 API 请求数。历史：2 起步实测稳定（3 并发触发 DeepSeek 限流→空响应→任务 error）；
# 429/空响应改为 Retry-After 排队重试后提到 4；个别端点仍严重限流可用环境变量
# GAMETR_TRANSLATE_CONCURRENCY 调小（真机调优免改码重打包）
_CONCURRENCY = int(os.getenv("GAMETR_TRANSLATE_CONCURRENCY", "4"))
# 提示词语义版本：参与翻译缓存 key——改提示词后旧缓存自动失效（只增不改，重打 sidecar 即新值）
_CACHE_PROMPT_VERSION = "1"

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


def _cache_key(source: str, *, provider_id: str, model: str,
               glossary_version: str) -> str:
    """翻译缓存 key：语义条件全参与——换模型/改术语/改提示词 → key 变 → 旧缓存自然失效。"""
    payload = "\x1f".join((provider_id, model or "", glossary_version or "",
                           _CACHE_PROMPT_VERSION, source))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _group_translatable(
    entries: list[Entry], items: list[ProtectedItem],
) -> tuple[list[list[tuple[Entry, ProtectedItem]]], list[ProtectedItem]]:
    """分流可译条目并**同源分组**；纯标签条目（保护后无可译文本）单独列出。

    返回 (可译同源组, 纯标签条目)。组内条目 source 完全相同 → 保护器确定性保证
    protected/tokens 一致，一次请求即可；防御非确定性保护器：同源但 protected 不一致时
    拆成独立组照常各请求一次。
    """
    by_src: dict[str, list[tuple[Entry, ProtectedItem]]] = {}
    non: list[ProtectedItem] = []
    for e, it in zip(entries, items, strict=True):
        if _has_translatable_text(it.protected):
            by_src.setdefault(e.source, []).append((e, it))
        else:
            non.append(it)
    groups: list[list[tuple[Entry, ProtectedItem]]] = []
    for members in by_src.values():
        proto = members[0][1].protected
        if all(m[1].protected == proto for m in members):
            groups.append(members)
        else:
            groups.extend([m] for m in members)
    return groups, non


async def _call_and_validate(
    provider: TranslationProvider,
    items: list[ProtectedItem],
    *,
    model: str | None, api_key: str | None, glossary: str | None,
    few_shot: list[tuple[str, str]] | None, speaker: str | None,
    restore: Callable[[str, list[str]], str], has_ph: Callable[[str], bool],
) -> tuple[dict[str, str], list[ProtectedItem], list[str], int, int]:
    """调 provider 一次，逐条校验+还原。返回 ({id: 还原译文}, 失败条目, 失败警告, tokens_in, tokens_out)。"""
    batch = [TranslateItem(id=i.id, text=i.protected) for i in items]
    results = await provider.translate_batch(
        batch, model=model, api_key=api_key, glossary=glossary,
        few_shot=few_shot, speaker=speaker,
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
    cache_scope: tuple[str, str, str] | None = None,
) -> tuple[int, list[str]]:
    """逐批翻译。返回 (翻译成功条数, 失败警告列表)。不抛错（失败记 warning）。

    on_usage(tokens_in, tokens_out)：累计上报用量（runner 落 translate_usage 供 stats）。
    cache_scope=(provider_id, model, glossary_version)：启用翻译缓存（同条件复用上次成功
    译文）；**None = 不用缓存**——translate.retranslate_entries（重翻坏结果）必须传 None，
    否则缓存命中返回的还是上次的坏译文（行数不匹配等），白翻一遍。
    """
    p, r, h = get_protector_fns(protector)
    cancel = should_cancel or (lambda: False)
    total = len(entries)
    done = 0
    translated = 0
    tokens_in = tokens_out = 0
    final_warnings: list[str] = []
    batches = make_batches(entries)

    repo: Repo | None = getattr(project, "repo", None)
    # 组批后补上下文：说话人 + 同文件已确认译文 few-shot（实现于 M3，此处接线启用；
    # 每次翻译只有新确认的增量，示例来自翻译时刻库里已确认内容）
    if batches and repo is not None:
        fill_speaker_and_few_shot(batches, repo)

    def cache_get(src: str) -> str | None:
        if repo is None or cache_scope is None:
            return None
        return repo.translation_cache_get(_cache_key(
            src, provider_id=cache_scope[0], model=cache_scope[1],
            glossary_version=cache_scope[2],
        ))

    def cache_put(src: str, result: str) -> None:
        if repo is None or cache_scope is None:
            return
        repo.translation_cache_put(
            cache_key=_cache_key(src, provider_id=cache_scope[0],
                                 model=cache_scope[1], glossary_version=cache_scope[2]),
            source=src, result=result, provider_id=cache_scope[0], model=cache_scope[1],
        )

    # 并发翻译：批间 Semaphore 并发调 Provider（httpx await 让出事件循环，单线程安全；
    # sqlite 落库与共享计数在非 await 段串行执行，无竞态）
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def process_batch(batch: Batch) -> None:
        nonlocal done, translated, tokens_in, tokens_out, final_warnings
        if cancel():
            return  # 批边界取消
        try:
            async with sem:
                restored: dict[str, str] = {}
                items = protect_all(batch.entries, p)
                groups, non = _group_translatable(batch.entries, items)
                # 纯标签条目（保护后无可译文本）→ 原文还原落库，不进 AI
                for it in non:
                    restored[it.id] = r(it.protected, it.tokens)

                # 缓存命中：同条件上次已译 → 直接复用（0 API 成本）；未命中才调 AI
                todo: list[list[tuple[Entry, ProtectedItem]]] = []
                for g in groups:
                    src = g[0][0].source
                    hit = cache_get(src)
                    if hit is not None:
                        for e, _it in g:
                            restored[e.id] = hit
                    else:
                        todo.append(g)

                if todo:
                    # 每组只发组首条目（representative）；失败重试语义保持：重试 1 次
                    # 仍失败 → 保留原文落库（游戏安全优先）+ warning
                    rep_items = [g[0][1] for g in todo]
                    ok_by_id: dict[str, str] = {}
                    fallback_origins: set[str] = set()
                    r1, failed, _w1, ti, to = await _call_and_validate(
                        provider, rep_items, model=model, api_key=api_key,
                        glossary=glossary, few_shot=batch.few_shot, speaker=batch.speaker,
                        restore=r, has_ph=h,
                    )
                    tokens_in += ti
                    tokens_out += to
                    ok_by_id.update(r1)
                    if failed and _MAX_RETRY > 0:
                        r2, failed2, warns2, ti2, to2 = await _call_and_validate(
                            provider, failed, model=model, api_key=api_key,
                            glossary=glossary, few_shot=batch.few_shot, speaker=batch.speaker,
                            restore=r, has_ph=h,
                        )
                        tokens_in += ti2
                        tokens_out += to2
                        ok_by_id.update(r2)
                        final_warnings.extend(warns2)  # 重试后仍失败 → warning
                        for it in failed2:
                            # 重试后仍失败（AI 未译/内容不可译如 JS/ID）→ 保留原文落库，
                            # 避免永远待译（游戏安全优先：原文在游戏里总比错译好）
                            fallback_origins.add(it.id)
                            ok_by_id[it.id] = r(it.protected, it.tokens)
                            final_warnings.append(
                                f"{it.id}: 保留原文（AI 未译或内容不可译，如插件配置/JS/ID）"
                            )
                    for g in todo:
                        rep = g[0][1]
                        text = ok_by_id.get(rep.id)
                        if text is None:
                            continue  # 与旧语义一致：失败无结果 → 该组不落库，留待下次
                        for e, _it in g:
                            restored[e.id] = text
                        # 只缓存真译文；保留原文的兜底结果不入缓存（否则永远不再重试）
                        if rep.id not in fallback_origins:
                            cache_put(g[0][0].source, text)
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
