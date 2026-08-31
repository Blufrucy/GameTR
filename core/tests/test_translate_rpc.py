"""translate.* / glossary.delete RPC 端到端（M3 任务管理）。

直接驱动 _process_line_async + 手动 ctx（project 注入），后台任务用 sleep 收尾。
translate.start 后台协程 + progress 通知的 serve_stdio 集成已在 test_framing 覆盖。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from gt_core.ir import entry_id
from gt_core.project import Project
from gt_core.providers import ProviderManager
from gt_core.providers.mock import MockProvider
from gt_core.rpc.methods import (
    _set_provider_manager,
    register_core_methods,
)
from gt_core.rpc.models import Entry, EntryStatus
from gt_core.rpc.server import _process_line_async


def _mgr_with_mock() -> ProviderManager:
    """测试注入：空 ProviderManager + MockProvider（产品不含 mock，测试用）。"""
    mgr = ProviderManager()
    mgr._providers["mock"] = MockProvider()  # type: ignore[attr-defined]  # 测试注入
    return mgr


def _setup_project(tmp_path: Path) -> Project:
    project = Project.create(tmp_path / "p.sqlite3", engine_id="rpgmv", source_path="x")
    entries = [
        Entry(id=entry_id("rpgmv", f"loc{i}", src), source=src, translation=None,
              status=EntryStatus.PENDING, locator=f"loc{i}",
              context_json=f'{{"file_path": "Map001.json", "order": {i}}}',
              updated_at=0.0)
        for i, src in enumerate(["こんにちは、\\N[1]勇者！", "回復薬を買う", "はい"])
    ]
    project.repo.upsert_entries(entries)
    return project


def _req(method: str, params: dict | None = None, rid: int = 1) -> str:
    req: dict = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        req["params"] = params
    return json.dumps(req, ensure_ascii=False)


def test_translate_start_rpc_end_to_end(tmp_path):
    _set_provider_manager(_mgr_with_mock())
    try:
        project = _setup_project(tmp_path)
        reg = register_core_methods()
        ctx = {"project": project}

        async def run():
            resp = await _process_line_async(
                _req("translate.start", {"scope": "all", "provider_id": "mock"}), reg, ctx
            )
            await asyncio.sleep(0.05)  # 等后台 mock 任务收尾
            return resp

        resp = asyncio.run(run())
        assert "result" in resp, resp
        task = resp["result"]
        assert task["task_id"] and task["status"] == "running"
        # 条目落库（Mock 前缀 + 原文，占位符保留）
        row = project.repo.get(entry_id("rpgmv", "loc0", "こんにちは、\\N[1]勇者！"))
        assert row.translation == "【译】こんにちは、\\N[1]勇者！"
        assert row.status == EntryStatus.MACHINE
    finally:
        _set_provider_manager(None)


def test_translate_status_and_stats(tmp_path):
    _set_provider_manager(_mgr_with_mock())
    try:
        project = _setup_project(tmp_path)
        reg = register_core_methods()
        ctx = {"project": project}

        async def run():
            await _process_line_async(
                _req("translate.start", {"scope": "all", "provider_id": "mock"}), reg, ctx
            )
            await asyncio.sleep(0.05)
            status = await _process_line_async(_req("translate.status", rid=2), reg, ctx)
            stats = await _process_line_async(_req("translate.stats", rid=3), reg, ctx)
            return status, stats

        status, stats = asyncio.run(run())
        assert status["result"]["status"] == "done"
        assert status["result"]["done"] == 3
        assert stats["result"]["tokens_in"] > 0 and stats["result"]["provider_id"] == "mock"
    finally:
        _set_provider_manager(None)


def test_translate_cancel_missing_task(tmp_path):
    _set_provider_manager(_mgr_with_mock())
    try:
        project = _setup_project(tmp_path)
        reg = register_core_methods()
        ctx = {"project": project}

        async def run():
            return await _process_line_async(
                _req("translate.cancel", {"task_id": "nope"}), reg, ctx
            )

        resp = asyncio.run(run())
        assert resp["error"]["code"] == -32005  # TRANSLATE_NOT_RUNNING
    finally:
        _set_provider_manager(None)


def test_translate_requires_project(tmp_path):
    _set_provider_manager(_mgr_with_mock())
    try:
        reg = register_core_methods()

        async def run():
            return await _process_line_async(
                _req("translate.start", {"scope": "all", "provider_id": "mock"}), reg, {}
            )

        resp = asyncio.run(run())
        assert resp["error"]["code"] == -32002  # NO_PROJECT
    finally:
        _set_provider_manager(None)


def test_glossary_delete_rpc(tmp_path):
    _set_provider_manager(_mgr_with_mock())
    try:
        project = _setup_project(tmp_path)
        project.repo.glossary_upsert("勇者", "hero")
        reg = register_core_methods()
        ctx = {"project": project}

        async def run():
            return await _process_line_async(
                _req("glossary.delete", {"term": "勇者"}), reg, ctx
            )

        resp = asyncio.run(run())
        assert resp["result"]["deleted"] == 1
        assert project.repo.glossary_list() == []
    finally:
        _set_provider_manager(None)
