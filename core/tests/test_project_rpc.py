"""RPC 方法端到端单测（M1）：project/entries/glossary 全方法 + 参数校验 + 错误码。

模拟 curl 风格：逐行 JSON 发给 _process_line，检查响应。
"""

import json

import pytest

from gt_core.ir import entry_id
from gt_core.rpc.errors import RpcErrorCode
from gt_core.rpc.methods import register_core_methods
from gt_core.rpc.models import Entry, EntryStatus
from gt_core.rpc.server import _process_line


@pytest.fixture()
def reg():
    return register_core_methods()


@pytest.fixture()
def ctx():
    return {}


def call(line: str, reg, ctx) -> dict | None:
    return _process_line(line, reg, ctx)


def _proj_path(tmp_path):
    return str(tmp_path / "proj.sqlite3")


# ---------- project.* ----------

def test_project_create_returns_info(reg, ctx, tmp_path):
    resp = call(json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "project.create",
        "params": {"path": _proj_path(tmp_path), "engine_id": "rpgmv", "source_path": "/game"},
    }), reg, ctx)
    assert resp["id"] == 1
    info = resp["result"]
    assert info["engine_id"] == "rpgmv"
    assert info["schema_version"] == 4
    assert info["project_state"] == "created"


def test_project_create_missing_param(reg, ctx):
    resp = call('{"jsonrpc":"2.0","id":1,"method":"project.create","params":{"path":"x"}}', reg, ctx)
    assert resp["error"]["code"] == RpcErrorCode.INVALID_PARAMS


def test_project_create_existing(reg, ctx, tmp_path):
    call(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "project.create",
                     "params": {"path": _proj_path(tmp_path), "engine_id": "e", "source_path": "s"}}), reg, ctx)
    resp = call(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "project.create",
                            "params": {"path": _proj_path(tmp_path), "engine_id": "e", "source_path": "s"}}), reg, ctx)
    assert resp["error"]["code"] == RpcErrorCode.PROJECT_ERROR


def test_project_open_close(reg, ctx, tmp_path):
    from pathlib import Path
    path = _proj_path(tmp_path)
    call(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "project.create",
                     "params": {"path": path, "engine_id": "e", "source_path": "s"}}), reg, ctx)
    call('{"jsonrpc":"2.0","id":2,"method":"project.close"}', reg, ctx)
    resp = call(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "project.open",
                            "params": {"path": path}}), reg, ctx)
    assert Path(resp["result"]["path"]) == Path(path).resolve()
    assert resp["result"]["engine_id"] == "e"


def test_project_open_missing(reg, ctx, tmp_path):
    resp = call(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "project.open",
                            "params": {"path": str(tmp_path / "nope.sqlite3")}}), reg, ctx)
    assert resp["error"]["code"] == RpcErrorCode.PROJECT_ERROR


def test_project_stats_no_project(reg, ctx):
    resp = call('{"jsonrpc":"2.0","id":1,"method":"project.stats"}', reg, ctx)
    assert resp["error"]["code"] == RpcErrorCode.NO_PROJECT


# ---------- entries.* ----------

@pytest.fixture()
def opened(reg, ctx, tmp_path):
    """创建并打开项目，返回 (path, ids)。"""
    path = _proj_path(tmp_path)
    call(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "project.create",
                     "params": {"path": path, "engine_id": "rpgmv", "source_path": "/g"}}), reg, ctx)
    entries = [Entry(
        id=entry_id("rpgmv", f"loc:{i}", f"勇者テキスト{i}"),
        source=f"勇者テキスト{i}", translation=None,
        status=EntryStatus.PENDING, locator=f"loc:{i}",
        context_json='{"file_path": "Map001.json"}', updated_at=0.0,
    ) for i in range(5)]
    ctx["project"].repo.upsert_entries(entries)
    return ctx["project"], [e.id for e in entries]


def test_entries_list_paging(reg, ctx, opened):
    resp = call('{"jsonrpc":"2.0","id":1,"method":"entries.list","params":{"page":1,"page_size":2}}', reg, ctx)
    page = resp["result"]
    assert page["total"] == 5 and len(page["items"]) == 2
    assert page["items"][0]["status"] == 1  # EntryStatus.PENDING 序列化为 int


def test_entries_list_status_filter(reg, ctx, opened):
    project, ids = opened
    project.repo.batch_update_status([ids[0]], EntryStatus.EDITED)
    resp = call('{"jsonrpc":"2.0","id":1,"method":"entries.list","params":{"status":3}}', reg, ctx)
    assert resp["result"]["total"] == 1


def test_entries_get(reg, ctx, opened):
    _, ids = opened
    resp = call(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "entries.get", "params": {"id": ids[0]}}), reg, ctx)
    assert resp["result"]["id"] == ids[0]
    assert "locator" in resp["result"]


def test_entries_get_missing(reg, ctx, opened):
    resp = call('{"jsonrpc":"2.0","id":1,"method":"entries.get","params":{"id":"nope"}}', reg, ctx)
    assert resp["error"]["code"] == RpcErrorCode.PROJECT_ERROR


def test_entries_update_translation_and_status(reg, ctx, opened):
    _, ids = opened
    resp = call(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "entries.update",
                            "params": {"id": ids[0], "translation": "译文", "status": 2}}), reg, ctx)
    e = resp["result"]
    assert e["translation"] == "译文" and e["status"] == 2


def test_entries_update_illegal_status(reg, ctx, opened):
    _, ids = opened
    resp = call(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "entries.update",
                            "params": {"id": ids[0], "status": 4}}), reg, ctx)
    assert resp["error"]["code"] == RpcErrorCode.PROJECT_ERROR  # PENDING->CONFIRMED 非法


def test_entries_batch_update_status(reg, ctx, opened):
    _, ids = opened
    resp = call(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "entries.batch_update_status",
                            "params": {"ids": ids[:3], "status": 2}}), reg, ctx)
    assert resp["result"]["updated"] == 3


def test_entries_batch_skips_confirmed(reg, ctx, opened):
    project, ids = opened
    # 推进 ids[0] 到 CONFIRMED
    for s in (EntryStatus.MACHINE, EntryStatus.EDITED, EntryStatus.CONFIRMED):
        project.repo.update(ids[0], status=s)
    # 批量改 MACHINE：CONFIRMED 的 ids[0] 跳过，其余 4 条 PENDING->MACHINE
    resp = call(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "entries.batch_update_status",
                            "params": {"ids": ids, "status": 2}}), reg, ctx)
    assert resp["result"]["updated"] == 4  # CONFIRMED 那条跳过


def test_entries_search(reg, ctx, opened):
    resp = call('{"jsonrpc":"2.0","id":1,"method":"entries.search","params":{"query":"勇者テキスト"}}', reg, ctx)
    assert resp["result"]["total"] == 5


def test_entries_requires_project(reg, ctx):
    resp = call('{"jsonrpc":"2.0","id":1,"method":"entries.list"}', reg, ctx)
    assert resp["error"]["code"] == RpcErrorCode.NO_PROJECT


def test_entries_bad_param_extra_field(reg, ctx, opened):
    resp = call('{"jsonrpc":"2.0","id":1,"method":"entries.list","params":{"page":1,"bogus":1}}', reg, ctx)
    assert resp["error"]["code"] == RpcErrorCode.INVALID_PARAMS  # extra=forbid


# ---------- glossary.* ----------

def test_glossary_roundtrip(reg, ctx, opened):
    resp = call('{"jsonrpc":"2.0","id":1,"method":"glossary.upsert","params":{"term":"勇者","translation":"hero"}}', reg, ctx)
    assert resp["result"]["term"] == "勇者" and resp["result"]["id"] >= 1
    call('{"jsonrpc":"2.0","id":2,"method":"glossary.upsert","params":{"term":"魔王","translation":"demon lord"}}', reg, ctx)
    resp = call('{"jsonrpc":"2.0","id":3,"method":"glossary.list"}', reg, ctx)
    assert len(resp["result"]) == 2


# ---------- core.* 保持可用 ----------

def test_core_still_works(reg, ctx, opened):
    resp = call('{"jsonrpc":"2.0","id":1,"method":"core.ping"}', reg, ctx)
    assert resp["result"]["pong"] is True
