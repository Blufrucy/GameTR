"""存储层 DAO 单测（路线图 1.3）：批量插入/分页/过滤/FTS/状态守卫/glossary。"""


import pytest

from gt_core.ir import entry_id
from gt_core.pipeline import InvalidStateTransition
from gt_core.project import Project
from gt_core.rpc.models import Entry, EntryStatus


@pytest.fixture()
def project(tmp_path):
    p = Project.create(tmp_path / "proj.sqlite3", engine_id="rpgmv", source_path="/fake")
    yield p
    p.close()


def _entry(i: int, *, text: str = None, status: EntryStatus = EntryStatus.PENDING,
           locator: str = None, context: str = None) -> Entry:
    text = text or f"text-{i} こんにちは勇者{i}"
    loc = locator or f"loc:{i}"
    return Entry(
        id=entry_id("rpgmv", loc, text),
        source=text,
        translation=None,
        status=status,
        locator=loc,
        context_json=context or '{"file_path": "Map001.json"}',
        updated_at=0.0,
    )


def _seed(project, n: int = 20) -> list[Entry]:
    entries = [_entry(i) for i in range(n)]
    project.repo.upsert_entries(entries)
    return entries


def _advance(project, eid: str, *statuses: EntryStatus) -> None:
    """把条目逐级推进到目标状态（每步都走状态机守卫）。"""
    for s in statuses:
        project.repo.update(eid, status=s)


class TestUpsert:
    def test_bulk_insert_and_count(self, project):
        _seed(project, 100)
        assert project.repo.count() == 100

    def test_upsert_same_id_updates(self, project):
        """显式指定同 id：第二次 upsert 覆盖旧内容（文本变了会换 id，故这里手工钉 id）。"""
        e = _entry(0, text="original")
        project.repo.upsert_entries([e])
        e2 = Entry(
            id=e.id,  # 同 id = 更新语义
            source="changed", translation="訳文",
            status=EntryStatus.PENDING, locator=e.locator, updated_at=0.0,
        )
        project.repo.upsert_entries([e2])
        assert project.repo.count() == 1
        got = project.repo.get(e.id)
        assert got.source == "changed"
        assert got.translation == "訳文"

    def test_upsert_empty(self, project):
        assert project.repo.upsert_entries([]) == 0


class TestGetList:
    def test_get_missing_returns_none(self, project):
        assert project.repo.get("nope") is None

    def test_list_pages(self, project):
        _seed(project, 50)
        p1 = project.repo.list_entries(page=1, page_size=20)
        p2 = project.repo.list_entries(page=2, page_size=20)
        assert p1.total == 50 and len(p1.items) == 20
        assert len(p2.items) == 20
        ids1 = {e.id for e in p1.items}
        assert not (ids1 & {e.id for e in p2.items})  # 两页无重叠

    def test_list_status_filter(self, project):
        _seed(project, 10)
        project.repo.batch_update_status([_entry(i).id for i in range(3)], EntryStatus.EDITED)
        page = project.repo.list_entries(status=EntryStatus.EDITED, page_size=100)
        assert page.total == 3
        assert all(e.status is EntryStatus.EDITED for e in page.items)

    def test_list_file_path_filter(self, project):
        e = _entry(0, context='{"file_path": "Map001.json"}')
        e2 = _entry(1, context='{"file_path": "CommonEvents.json"}')
        project.repo.upsert_entries([e, e2])
        page = project.repo.list_entries(file_path="Map001.json")
        assert page.total == 1 and page.items[0].id == e.id

    def test_entry_roundtrip_locator(self, project):
        e = _entry(7, locator="$.events[0].pages[0].list[1].parameters[0]")
        project.repo.upsert_entries([e])
        got = project.repo.get(e.id)
        assert got.locator == e.locator  # locator 只存不解析（ADR-0003）


class TestUpdate:
    def test_update_translation(self, project):
        e = _entry(0)
        project.repo.upsert_entries([e])
        got = project.repo.update(e.id, translation="翻訳テキスト")
        assert got.translation == "翻訳テキスト"

    def test_update_status_forward(self, project):
        e = _entry(0)
        project.repo.upsert_entries([e])
        got = project.repo.update(e.id, status=EntryStatus.MACHINE)
        assert got.status is EntryStatus.MACHINE

    def test_update_status_illegal_raises(self, project):
        e = _entry(0)
        project.repo.upsert_entries([e])
        with pytest.raises(InvalidStateTransition):
            project.repo.update(e.id, status=EntryStatus.CONFIRMED)  # 跳过 MACHINE

    def test_update_missing_returns_none(self, project):
        assert project.repo.update("nope", translation="x") is None


class TestBatchStatus:
    def test_batch_confirmed_skipped(self, project):
        """一键重翻不得覆盖 CONFIRMED（路线图硬性要求）。"""
        e1, e2, e3 = _entry(0), _entry(1), _entry(2)
        project.repo.upsert_entries([e1, e2, e3])
        # 把 e2 推进到 CONFIRMED
        _advance(project, e2.id, EntryStatus.MACHINE, EntryStatus.EDITED, EntryStatus.CONFIRMED)
        # 批量重翻回 PENDING：CONFIRMED 的 e2 必须跳过，e1/e3 从 PENDING 变 PENDING 无操作
        updated = project.repo.batch_update_status([e1.id, e2.id, e3.id], EntryStatus.MACHINE)
        assert updated == 2  # 只有 e1/e3 被改（PENDING->MACHINE）
        assert project.repo.get(e1.id).status is EntryStatus.MACHINE
        assert project.repo.get(e2.id).status is EntryStatus.CONFIRMED  # 重翻不覆盖

    def test_batch_all_confirmed_returns_zero(self, project):
        e = _entry(0)
        project.repo.upsert_entries([e])
        project.repo.batch_update_status([e.id], EntryStatus.CONFIRMED)
        assert project.repo.batch_update_status([e.id], EntryStatus.MACHINE) == 0

    def test_batch_empty(self, project):
        assert project.repo.batch_update_status([], EntryStatus.PENDING) == 0


class TestSearch:
    def test_fts_three_chars_hits(self, project):
        """>=3 字符走 FTS trigram：子串命中。"""
        _seed(project, 10)
        page = project.repo.search("勇者0", page_size=20)  # 4 字，trigram 可匹配
        assert page.total >= 1
        assert all("勇者0" in e.source for e in page.items)

    def test_like_short_query(self, project):
        """<3 字符降级 LIKE：2 字 CJK 词可搜。"""
        _seed(project, 10)
        page = project.repo.search("勇者", page_size=20)
        assert page.total == 10

    def test_search_translation_field(self, project):
        e = _entry(0, text="original text")
        e.translation = "翻訳 勇者ワールド"
        project.repo.upsert_entries([e])
        page = project.repo.search("勇者ワールド", page_size=20)
        assert page.total == 1

    def test_search_no_match(self, project):
        _seed(project, 5)
        assert project.repo.search("不存在のキーワードXYZ", page_size=20).total == 0

    def test_search_like_escapes_wildcards(self, project):
        e = _entry(0, text="literal 100% real")
        project.repo.upsert_entries([e])
        # 查询含 % 不应被当作通配符
        page = project.repo.search("100", page_size=20)
        assert page.total == 1

    def test_search_paging(self, project):
        _seed(project, 50)
        p1 = project.repo.search("勇者", page=1, page_size=10)
        p2 = project.repo.search("勇者", page=2, page_size=10)
        assert p1.total == 50 and len(p1.items) == 10
        assert len(p2.items) == 10


class TestGlossary:
    def test_upsert_and_list(self, project):
        g = project.repo.glossary_upsert("勇者", "hero")
        assert g.id >= 1
        g2 = project.repo.glossary_upsert("勇者", "the Hero", match_case=True)
        assert g2.id == g.id  # 同 term 更新不新增
        lst = project.repo.glossary_list()
        assert len(lst) == 1
        assert lst[0].translation == "the Hero" and lst[0].match_case is True

    def test_upsert_two_terms(self, project):
        project.repo.glossary_upsert("魔女", "witch")
        project.repo.glossary_upsert("魔王", "demon lord")
        assert len(project.repo.glossary_list()) == 2


class TestStats:
    def test_stats_distribution(self, project):
        e1, e2, e3 = _entry(0), _entry(1), _entry(2)
        project.repo.upsert_entries([e1, e2, e3])
        _advance(project, e1.id, EntryStatus.MACHINE, EntryStatus.EDITED, EntryStatus.CONFIRMED)
        stats = project.repo.stats()
        assert stats.total == 3
        assert stats.by_status[str(int(EntryStatus.CONFIRMED))] == 1
        assert stats.by_status[str(int(EntryStatus.PENDING))] == 2


class TestPersistReopen:
    def test_save_and_reopen(self, project, tmp_path):
        """保存重开：数据、译文、状态全部保留。"""
        _seed(project, 10)
        e = project.repo.get(project.repo.list_entries(page_size=1).items[0].id)
        project.repo.update(e.id, translation="永続する訳文")
        _advance(project, e.id, EntryStatus.MACHINE, EntryStatus.EDITED)
        path = project.path
        project.close()

        reopened = Project.open(path)
        try:
            assert reopened.repo.count() == 10
            got = reopened.repo.get(e.id)
            assert got.translation == "永続する訳文"
            assert got.status is EntryStatus.EDITED
        finally:
            reopened.close()

    def test_info_metadata(self, project):
        info = project.info()
        assert info.engine_id == "rpgmv"
        assert info.schema_version == 4
        assert info.project_state.value == "created"

    def test_create_existing_raises(self, project):
        with pytest.raises(FileExistsError):
            Project.create(project.path, engine_id="x", source_path="y")

    def test_open_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            Project.open(tmp_path / "missing.sqlite3")


class TestMachineBaseline:
    """M4 机翻基线（machine_text）：AI 落库即记基线；人改只动 translation 不动基线；
    机翻·已改可恢复；清空回待译基线一并清掉。"""

    def _seed_pending(self, project, eid: str) -> Entry:
        project.repo.upsert_entries([Entry(
            id=eid, source="勇者 こんにちは", translation=None,
            status=EntryStatus.PENDING, locator="loc:x",
            context_json='{"file_path": "Map001.json"}', updated_at=0.0,
        )])
        return project.repo.get(eid)

    def test_machine_write_records_baseline(self, project):
        eid = entry_id("rpgmv", "loc:x", "勇者 こんにちは")
        self._seed_pending(project, eid)
        # AI 落库（upsert_translations，等价流水线 Persister）
        project.repo.upsert_translations([Entry(
            id=eid, source="勇者 こんにちは", translation="勇者，你好",
            status=EntryStatus.MACHINE, locator="loc:x",
            context_json='{"file_path": "Map001.json"}', updated_at=1.0,
        )])
        got = project.repo.get(eid)
        assert got.translation == "勇者，你好"
        assert got.machine_text == "勇者，你好"  # 基线 = AI 输出

    def test_human_edit_keeps_baseline_and_revert(self, project):
        eid = entry_id("rpgmv", "loc:x", "勇者 こんにちは")
        self._seed_pending(project, eid)
        project.repo.upsert_translations([Entry(
            id=eid, source="勇者 こんにちは", translation="勇者，你好",
            status=EntryStatus.MACHINE, locator="loc:x",
            context_json='{"file_path": "Map001.json"}', updated_at=1.0,
        )])
        # 人工编辑：translation 变了，基线保留
        edited = project.repo.update(eid, translation="勇者，嗨！", edited=1)
        assert edited.translation == "勇者，嗨！"
        assert edited.edited == 1
        assert edited.machine_text == "勇者，你好"
        # 恢复机翻：translation 回到基线 + edited 归 0
        reverted = project.repo.update(eid, translation=edited.machine_text, edited=0)
        assert reverted.translation == "勇者，你好"
        assert reverted.edited == 0
        assert reverted.machine_text == "勇者，你好"

    def test_clear_translation_goes_pending_and_drops_baseline(self, project):
        eid = entry_id("rpgmv", "loc:x", "勇者 こんにちは")
        self._seed_pending(project, eid)
        project.repo.upsert_translations([Entry(
            id=eid, source="勇者 こんにちは", translation="勇者，你好",
            status=EntryStatus.MACHINE, locator="loc:x",
            context_json='{"file_path": "Map001.json"}', updated_at=1.0,
        )])
        cleared = project.repo.update(eid, translation=None, status=EntryStatus.PENDING,
                                     edited=0)
        assert cleared.translation is None
        assert cleared.status is EntryStatus.PENDING
        assert cleared.machine_text is None  # 无译文就无基线（避免陈旧 AI 原文残留）

    def test_clear_legacy_edited_entry_goes_pending(self, project):
        """旧版已改条目（状态 3 EDITED，M4 机翻基线改造前落库）清空也应回待译。

        （用户实测 bug：2→1 放行后已改译文清空仍报「非法状态迁移: 3 -> 1」）
        """
        eid = entry_id("rpgmv", "loc:x", "勇者 こんにちは")
        self._seed_pending(project, eid)
        project.repo.upsert_translations([Entry(
            id=eid, source="勇者 こんにちは", translation="勇者，你好",
            status=EntryStatus.EDITED, locator="loc:x",
            context_json='{"file_path": "Map001.json"}', updated_at=1.0,
        )])
        cleared = project.repo.update(eid, translation=None, status=EntryStatus.PENDING,
                                     edited=0)
        assert cleared.translation is None
        assert cleared.status is EntryStatus.PENDING
        assert cleared.machine_text is None

