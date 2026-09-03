"""翻译流水线单测（M3/M4）：ContextBatcher 分组、占位符阶段、pipeline 集成、缓存与去重。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from gt_core.ir import entry_id
from gt_core.project import Project
from gt_core.providers.base import TranslateResult
from gt_core.providers.mock import MockProvider
from gt_core.rpc.models import Entry, EntryStatus
from gt_core.translate.batcher import make_batches
from gt_core.translate.pipeline import translate_entries
from gt_core.translate.stages import protect_all, validate_result


def _entry(i: int, source: str, fp: str = "Map001.json", order: int | None = None) -> Entry:
    ctx = {"file_path": fp, "order": order if order is not None else i}
    return Entry(
        id=entry_id("rpgmv", f"loc{i}", source), source=source,
        translation=None, status=EntryStatus.PENDING, locator=f"loc{i}",
        context_json=json.dumps(ctx, ensure_ascii=False), updated_at=0.0,
    )


# ---------- ContextBatcher ----------

def test_batches_merge_small_files_keep_order():
    """贪心合并：小文件拼成大批（≤上限），同文件条目仍相邻（few-shot 稳定）。

    原按文件严格分桶 → 小文件每文件一批，批次爆炸（真实游戏 60 文件/2061 条实测坑）。
    """
    entries = [
        _entry(0, "A", fp="Map002.json", order=1),
        _entry(1, "B", fp="Map001.json", order=0),
        _entry(2, "C", fp="Map001.json", order=1),
        _entry(3, "D", fp="Map002.json", order=0),
    ]
    # 4 条 < 上限 → 合并成一个 batch（同文件相邻）
    batches = make_batches(entries)
    assert len(batches) == 1
    ids = [e.id for e in batches[0].entries]
    # 排序按 (file_path, order)：Map001 B,C 相邻 → Map002 D,A
    assert ids == [entries[1].id, entries[2].id, entries[3].id, entries[0].id]
    # 大文件超上限仍切块
    big = [_entry(i, "x", fp="Map001.json") for i in range(10)]
    b2 = make_batches(big, max_items=4, max_chars=10_000)
    assert [len(b.entries) for b in b2] == [4, 4, 2]


def test_batches_respect_item_and_char_limits():
    entries = [_entry(i, f"テキスト{i}", fp="Map001.json") for i in range(10)]
    # 条数上限 4
    batches = make_batches(entries, max_items=4, max_chars=10_000)
    assert [len(b.entries) for b in batches] == [4, 4, 2]
    # 字符上限 20：每批总字符不超过上限（单条超限时独立成批）
    batches2 = make_batches(entries, max_items=100, max_chars=20)
    assert all(sum(len(e.source) for e in b.entries) <= 20 for b in batches2)
    assert len(batches2) >= len(entries) // 4  # 10 条 5 字符 → 至少 3 批


# ---------- 占位符阶段 ----------

def test_protect_validate_restore_cycle():
    src = "こんにちは、\\N[1]勇者！"
    # 真实保护器（identity 模拟：此处验证 validate 对带哨兵译文的判定）
    items = protect_all([_entry(0, src)], lambda s: (s.replace("\\N[1]", "⟦0⟧"), ["\\N[1]"]))
    assert items[0].protected == "こんにちは、⟦0⟧勇者！"
    # 占位符一致 → 通过
    ok, warn = validate_result(items[0].protected, "你好，⟦0⟧勇者！", lambda t: "⟦" in t)
    assert ok and not warn
    # 占位符破坏 → 失败
    ok2, warn2 = validate_result(items[0].protected, "你好，勇者！", lambda t: "⟦" in t)
    assert not ok2 and "占位符" in warn2
    # 漏译（返回原文）→ 失败
    ok3, warn3 = validate_result(items[0].protected, items[0].protected, lambda t: True)
    assert not ok3 and "漏译" in warn3


# ---------- pipeline 集成 ----------

def test_translate_entries_mock_pipeline(tmp_path):
    project = Project.create(tmp_path / "p.sqlite3", engine_id="rpgmv", source_path="x")
    entries = [
        _entry(0, "こんにちは、\\N[1]勇者！"),
        _entry(1, "回復薬を買う"),
        _entry(2, "はい"),
    ]
    project.repo.upsert_entries(entries)
    progress: list[tuple[int, int]] = []

    async def run() -> tuple[int, list[str]]:
        return await translate_entries(
            project=project, entries=entries, provider=MockProvider(),
            model="mock-v1", api_key=None,
            notify=lambda done, total, msg: progress.append((done, total)),
        )

    translated, warns = asyncio.run(run())
    assert translated == 3 and warns == []
    # 落库检查：translation = Mock 前缀 + 原文本（占位符原样保留）
    for e in entries:
        row = project.repo.get(e.id)
        assert row.translation == f"【译】{e.source}"
        assert row.status == EntryStatus.MACHINE
    # progress 通知：done 递增
    assert progress[-1] == (3, 3)


def test_translate_entries_skips_confirmed(tmp_path):
    """重翻不覆盖 CONFIRMED（SQL 层原子跳过，数据不丢）。"""
    project = Project.create(tmp_path / "p.sqlite3", engine_id="rpgmv", source_path="x")
    entries = [_entry(0, "勇者の剣"), _entry(1, "HP回復")]
    project.repo.upsert_entries(entries)
    # 推进 entries[0] 到 CONFIRMED
    for s in (EntryStatus.MACHINE, EntryStatus.EDITED, EntryStatus.CONFIRMED):
        project.repo.update(entries[0].id, status=s)
    project.repo.update(entries[0].id, translation="人工确认译文")

    async def run():
        return await translate_entries(
            project=project, entries=entries, provider=MockProvider(),
            model="mock-v1", api_key=None,
        )

    translated, warns = asyncio.run(run())
    # CONFIRMED 那条跳过（不被翻译覆盖），另一条翻译
    assert translated == 1
    confirmed = project.repo.get(entries[0].id)
    assert confirmed.translation == "人工确认译文"  # 未被 Mock 覆盖
    assert confirmed.status == EntryStatus.CONFIRMED
    assert project.repo.get(entries[1].id).translation == "【译】HP回復"


# ---------- 同源去重 + 翻译缓存 + few-shot（M4 提速/提质） ----------

class SpyProvider:
    """记录每次 translate_batch 的参数（few_shot/speaker/ids），确定性回译。"""

    provider_id = "spy"
    display_name = "Spy（记录参数）"
    models = ["spy-v1"]
    needs_api_key = False
    supports_structured = False
    base_url: str | None = None

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def translate_batch(
        self, batch: list, *, model: str | None = None, api_key: str | None = None,
        glossary: str | None = None, few_shot: list[tuple[str, str]] | None = None,
        speaker: str | None = None,
    ) -> list[TranslateResult]:
        self.calls.append({
            "model": model, "glossary": glossary,
            "few_shot": few_shot, "speaker": speaker,
            "ids": [i.id for i in batch],
        })
        return [
            TranslateResult(id=i.id, translation=f"【译】{i.text}",
                            tokens_in=max(1, len(i.text)), tokens_out=1)
            for i in batch
        ]

    async def test(self, *, model: str | None = None,
                   api_key: str | None = None) -> tuple[bool, float, str]:
        return True, 0.0, "ok"

    async def list_models(self, api_key: str | None = None) -> list[str]:
        return ["spy-v1"]


def test_dedupe_identical_sources_calls_api_once(tmp_path):
    """同源条目（游戏重复台词/物品说明）只请求一次，译文复制到组内每条。"""
    project = Project.create(tmp_path / "p.sqlite3", engine_id="rpgmv", source_path="x")
    entries = [_entry(0, "はーい"), _entry(1, "はーい")]
    project.repo.upsert_entries(entries)
    spy = SpyProvider()

    translated, warns = asyncio.run(translate_entries(
        project=project, entries=entries, provider=spy,
        model="spy-v1", api_key=None,
    ))
    assert translated == 2 and warns == []
    assert len(spy.calls) == 1  # 同源两组实为一条请求
    for e in entries:
        assert project.repo.get(e.id).translation == "【译】はーい"


def test_cache_hit_reuses_and_skips_api(tmp_path):
    """翻译记忆：同 (provider, 模型, 术语版本, 原文) 第二次翻译直接复用，0 请求。"""
    project = Project.create(tmp_path / "p.sqlite3", engine_id="rpgmv", source_path="x")
    scope = ("spy", "spy-v1", "v1")
    src = "アイテムの説明"
    e1 = _entry(0, src)
    project.repo.upsert_entries([e1])

    spy1 = SpyProvider()
    t1, w1 = asyncio.run(translate_entries(
        project=project, entries=[e1], provider=spy1,
        model="spy-v1", api_key=None, cache_scope=scope,
    ))
    assert t1 == 1 and w1 == [] and len(spy1.calls) == 1

    # 同原文的新条目（断点续翻/重跑场景）→ 缓存命中，不再调 API
    e2 = _entry(1, src)
    project.repo.upsert_entries([e2])
    spy2 = SpyProvider()
    t2, w2 = asyncio.run(translate_entries(
        project=project, entries=[e2], provider=spy2,
        model="spy-v1", api_key=None, cache_scope=scope,
    ))
    assert t2 == 1 and w2 == [] and len(spy2.calls) == 0
    assert project.repo.get(e2.id).translation == project.repo.get(e1.id).translation


def test_cache_misses_when_scope_changes(tmp_path):
    """缓存条件变化（换模型/改术语表）→ key 变 → 必须重新翻译（旧译文语义失效）。"""
    project = Project.create(tmp_path / "p.sqlite3", engine_id="rpgmv", source_path="x")
    src = "アイテムの説明"

    e1 = _entry(0, src)
    project.repo.upsert_entries([e1])
    spy1 = SpyProvider()
    asyncio.run(translate_entries(
        project=project, entries=[e1], provider=spy1,
        model="spy-v1", api_key=None, cache_scope=("spy", "spy-v1", "v1"),
    ))
    e2 = _entry(1, src)
    project.repo.upsert_entries([e2])
    spy2 = SpyProvider()
    # 术语表版本变了（v1 → v2）→ 旧缓存不可用，重新翻译
    asyncio.run(translate_entries(
        project=project, entries=[e2], provider=spy2,
        model="spy-v1", api_key=None, cache_scope=("spy", "spy-v1", "v2"),
    ))
    assert len(spy2.calls) == 1


def test_few_shot_and_speaker_reach_provider(tmp_path):
    """同文件已确认译文 → few_shot；说话人随批传给 provider（译文一致性）。"""
    project = Project.create(tmp_path / "p.sqlite3", engine_id="rpgmv", source_path="x")
    # 种子：同文件一条已确认译文（few-shot 来源）
    c = _entry(9, "グッズ", fp="Map003.json")
    project.repo.upsert_entries([c])
    for s in (EntryStatus.MACHINE, EntryStatus.EDITED, EntryStatus.CONFIRMED):
        project.repo.update(c.id, status=s)
    project.repo.update(c.id, translation="礼物")
    # 目标条目：带说话人、同文件
    ctx = {"file_path": "Map003.json", "order": 0, "speaker": "町人"}
    e = Entry(
        id=entry_id("rpgmv", "locX", "もう一度来てね"), source="もう一度来てね",
        translation=None, status=EntryStatus.PENDING, locator="locX",
        context_json=json.dumps(ctx, ensure_ascii=False), updated_at=0.0,
    )
    project.repo.upsert_entries([e])
    spy = SpyProvider()

    translated, warns = asyncio.run(translate_entries(
        project=project, entries=[e], provider=spy, model="spy-v1", api_key=None,
    ))
    assert translated == 1 and warns == []
    assert spy.calls[0]["few_shot"] == [("グッズ", "礼物")]
    assert spy.calls[0]["speaker"] == "町人"
