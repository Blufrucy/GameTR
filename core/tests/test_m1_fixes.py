"""Review 修复回归测试（2026-08-28 workflow review 发现的缺陷逐一钉死）。

覆盖：
- 通知出错不应答（JSON-RPC 规范）
- entries.update 显式 translation=null 清空 / 空操作不刷 updated_at
- batch_update_status 超大 ids 分块不崩（SQLITE_LIMIT_VARIABLE_NUMBER）
- file_path 过滤遇 malformed JSON 不崩
- 迁移原子性（失败不留半成品）+ 未来版本守卫
- FTS 同步：status-only 更新后搜索仍正常
- 无参方法多余字段被拒
"""

import json
import sqlite3

import pytest

from gt_core.ir import entry_id
from gt_core.project import Project
from gt_core.project.migrator import MigrationError, current_version, migrate
from gt_core.rpc.errors import RpcErrorCode
from gt_core.rpc.methods import register_core_methods
from gt_core.rpc.models import Entry, EntryStatus
from gt_core.rpc.server import _process_line


@pytest.fixture()
def project(tmp_path):
    p = Project.create(tmp_path / "p.sqlite3", engine_id="rpgmv", source_path="/g")
    yield p
    p.close()


def _e(i, **kw) -> Entry:
    text = kw.pop("source", f"text-{i} 勇者{i}")
    loc = kw.pop("locator", f"loc:{i}")
    trans = kw.pop("translation", None)
    ctxj = kw.pop("context_json", '{"file_path": "Map001.json"}')
    return Entry(
        id=entry_id("rpgmv", loc, text), source=text, translation=trans,
        status=EntryStatus.PENDING, locator=loc,
        context_json=ctxj, updated_at=0.0, **kw,
    )


# ---------- server: 通知错误不应答 ----------

def test_notification_error_returns_none():
    reg = register_core_methods()
    # 未知方法的通知（无 id）：规范要求绝不回复
    resp = _process_line('{"jsonrpc":"2.0","method":"no.such.method"}', reg, {})
    assert resp is None
    # 参数错误的通知同样不应答
    resp = _process_line('{"jsonrpc":"2.0","method":"entries.update","params":{"id":1}}', reg, {})
    assert resp is None


def test_request_error_still_answers():
    """带 id 的错误请求仍要应答（与通知相反）。"""
    reg = register_core_methods()
    resp = _process_line('{"jsonrpc":"2.0","id":7,"method":"no.such.method"}', reg, {})
    assert resp is not None and resp["error"]["code"] == RpcErrorCode.METHOD_NOT_FOUND


# ---------- repo: update 语义 ----------

def test_update_explicit_null_clears_translation(project):
    e = _e(0, translation=None)
    project.repo.upsert_entries([e])
    project.repo.update(e.id, translation="译")
    assert project.repo.get(e.id).translation == "译"
    project.repo.update(e.id, translation=None)  # 显式 null = 清空
    assert project.repo.get(e.id).translation is None


def test_update_noop_keeps_updated_at(project):
    e = _e(0)
    project.repo.upsert_entries([e])
    t1 = project.repo.get(e.id).updated_at
    project.repo.update(e.id)  # 空操作
    assert project.repo.get(e.id).updated_at == t1


def test_update_unchanged_value_keeps_updated_at(project):
    e = _e(0)
    project.repo.upsert_entries([e])
    project.repo.update(e.id, translation="x")
    t1 = project.repo.get(e.id).updated_at
    project.repo.update(e.id, translation="x")  # 值未变
    assert project.repo.get(e.id).updated_at == t1


# ---------- repo: batch 分块 ----------

def test_batch_large_ids_chunked(project):
    """40000 个 id 分块处理：不触发 SQLITE_LIMIT_VARIABLE_NUMBER 超限。"""
    n = 40_000
    project.repo.upsert_entries([_e(i) for i in range(n)])
    ids = [project.repo.list_entries(page_size=n).items[i].id for i in range(100)]
    ids += [f"missing-{i}" for i in range(39_900)]  # 混合不存在的 id
    updated = project.repo.batch_update_status(ids, EntryStatus.MACHINE)
    assert updated == 100  # 只有存在的且非 CONFIRMED 的被改


# ---------- repo: file_path 过滤鲁棒性 ----------

def test_file_path_filter_survives_malformed_json(project):
    good = _e(0, context_json='{"file_path": "Map001.json"}')
    bad = _e(1, context_json="not-json{{")
    project.repo.upsert_entries([good, bad])
    page = project.repo.list_entries(file_path="Map001.json")
    assert page.total == 1 and page.items[0].id == good.id  # 坏行按无匹配处理


# ---------- repo: FTS 与 status-only 更新 ----------

def test_fts_survives_status_only_update(project):
    e = _e(0, source="こんにちは勇者ワールド")
    project.repo.upsert_entries([e])
    project.repo.update(e.id, status=EntryStatus.MACHINE)  # status-only UPDATE（不重索引）
    hits = project.repo.search("勇者ワールド", page_size=20)
    assert hits.total == 1  # FTS 索引未被破坏


# ---------- migrator ----------

def test_migrate_atomic_on_failure(tmp_path):
    """迁移中途失败不留半成品（此前 executescript 非原子会留表 + 版本不动）。"""
    mig = tmp_path / "m"
    mig.mkdir()
    (mig / "001_init.sql").write_text(
        "CREATE TABLE a(x); SELECT * FROM no_such_table; CREATE TABLE b(y);",
        encoding="utf-8",
    )
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO meta VALUES ('schema_version','0')")
    with pytest.raises(sqlite3.OperationalError):
        migrate(conn, mig)
    # 表 a 不应存在（事务回滚）
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "a" not in tables and "b" not in tables
    assert current_version(conn) == 0
    conn.close()


def test_open_future_version_raises(tmp_path):
    """打开未来版本项目抛 MigrationError（-32003『版本不兼容』可达）。"""
    db = tmp_path / "future.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO meta VALUES ('schema_version','999')")
    conn.commit()
    conn.close()
    with pytest.raises(MigrationError):
        Project.open(db)


# ---------- RPC 层 ----------

def test_empty_params_rejects_extra(project, tmp_path):
    """无参方法（core.ping 等）带多余字段被拒（extra=forbid）。"""
    reg = register_core_methods()
    ctx = {}
    resp = _process_line(
        '{"jsonrpc":"2.0","id":1,"method":"core.ping","params":{"bogus":1}}', reg, ctx)
    assert resp["error"]["code"] == RpcErrorCode.INVALID_PARAMS


def test_update_null_via_rpc(tmp_path):
    """RPC 层 entries.update translation=null 清空译文。"""
    reg = register_core_methods()
    ctx: dict = {}
    path = str(tmp_path / "rpc.sqlite3")
    _process_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "project.create",
                              "params": {"path": path, "engine_id": "e", "source_path": "s"}}), reg, ctx)
    e = _e(0)
    ctx["project"].repo.upsert_entries([e])
    _process_line(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "entries.update",
                              "params": {"id": e.id, "translation": "译"}}), reg, ctx)
    resp = _process_line(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "entries.update",
                                     "params": {"id": e.id, "translation": None}}), reg, ctx)
    assert resp["result"]["translation"] is None


def test_update_null_status_rejected(tmp_path):
    """RPC 层 entries.update status=null 被拒（协议 status 非空枚举）。"""
    reg = register_core_methods()
    ctx: dict = {}
    path = str(tmp_path / "rpc.sqlite3")
    _process_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "project.create",
                              "params": {"path": path, "engine_id": "e", "source_path": "s"}}), reg, ctx)
    e = _e(0)
    ctx["project"].repo.upsert_entries([e])
    resp = _process_line(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "entries.update",
                                     "params": {"id": e.id, "status": None}}), reg, ctx)
    assert resp["error"]["code"] == RpcErrorCode.INVALID_PARAMS
