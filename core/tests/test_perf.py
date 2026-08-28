"""性能测试（路线图 1.3）：5 万条数据下分页 <50ms、FTS 搜索 <100ms。

验收场景 10 万条在 M1 验收脚本（core/tests/e2e）覆盖；这里按路线图指标用 5 万条。
数据一次性构建（session fixture），避免每个用例重复插入拖慢测试。
"""

import time

import pytest

from gt_core.ir import entry_id
from gt_core.project import Project
from gt_core.rpc.models import Entry, EntryStatus

_PERF_ROWS = 50_000


@pytest.fixture(scope="session")
def big_project(tmp_path_factory):
    """5 万条数据的一次性项目，供多个性能用例共享。"""
    db = tmp_path_factory.mktemp("perf") / "big.sqlite3"
    project = Project.create(db, engine_id="rpgmv", source_path="/perf")
    t0 = time.perf_counter()
    batch: list[Entry] = []
    for i in range(_PERF_ROWS):
        batch.append(Entry(
            id=entry_id("rpgmv", f"loc:{i}", f"こんにちは勇者{i} 世界の旅"),
            source=f"こんにちは勇者{i} 世界の旅",
            translation=None,
            status=EntryStatus.PENDING,
            locator=f"loc:{i}",
            context_json='{"file_path": "Map001.json"}',
            updated_at=0.0,
        ))
        if len(batch) >= 20_000:
            project.repo.upsert_entries(batch)
            batch = []
    if batch:
        project.repo.upsert_entries(batch)
    insert_s = time.perf_counter() - t0
    project._perf_insert_s = insert_s
    yield project
    project.close()


def test_bulk_insert_50k_speed(big_project):
    """插入 5 万条整体 <5s（路线图：2 万条 <2s，5 万条线性外推）。"""
    assert big_project._perf_insert_s < 5.0, f"插入 5 万条耗时 {big_project._perf_insert_s:.2f}s"


def test_page_latency_under_50ms(big_project):
    """entries.list 分页 <50ms（5 万条下）。"""
    t0 = time.perf_counter()
    page = big_project.repo.list_entries(page=100, page_size=200)
    ms = (time.perf_counter() - t0) * 1000
    assert page.total == _PERF_ROWS and len(page.items) == 200
    assert ms < 50, f"分页查询 {ms:.1f}ms 超过 50ms"


def test_fts_search_latency_under_100ms(big_project):
    """>=3 字符走 FTS trigram，<100ms（查询词为所有行共有子串，保证命中）。"""
    t0 = time.perf_counter()
    page = big_project.repo.search("世界の旅", page_size=50)
    ms = (time.perf_counter() - t0) * 1000
    assert page.total == _PERF_ROWS
    assert ms < 100, f"FTS 搜索 {ms:.1f}ms 超过 100ms"


def test_like_short_query_latency_under_100ms(big_project):
    """短查询（2 字）降级 LIKE，5 万条全表扫 <100ms。"""
    t0 = time.perf_counter()
    page = big_project.repo.search("勇者", page_size=50)
    ms = (time.perf_counter() - t0) * 1000
    assert page.total == _PERF_ROWS
    assert ms < 100, f"LIKE 搜索 {ms:.1f}ms 超过 100ms"


def test_status_filtered_page_latency(big_project):
    """按状态过滤的分页同样达标（索引 idx_entries_status）。"""
    t0 = time.perf_counter()
    page = big_project.repo.list_entries(status=EntryStatus.PENDING, page_size=100)
    ms = (time.perf_counter() - t0) * 1000
    assert page.total == _PERF_ROWS
    assert ms < 100, f"状态过滤分页 {ms:.1f}ms 超过 100ms"
