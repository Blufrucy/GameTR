"""翻译流水线单测（M3）：ContextBatcher 分组、占位符阶段、pipeline 集成。"""

from __future__ import annotations

import asyncio
import json

from gt_core.ir import entry_id
from gt_core.project import Project
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
