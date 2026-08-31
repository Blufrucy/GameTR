"""插件框架 + M2 RPC 单测（detect/extract/write_back/plugins.list）。

用 tmp_path 里的假插件验证加载契约与 RPC 管线；真实 RPGMV 插件在黄金样本测试
（tests/golden/rpgmv/）中端到端覆盖。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import gt_core.rpc.methods as methods
from gt_core.plugin import PLUGIN_API_VERSION, PluginManager
from gt_core.rpc.errors import RpcErrorCode
from gt_core.rpc.methods import register_core_methods
from gt_core.rpc.server import _process_line

# 假插件 adapter 源码（真插件结构参考：plugin.json + adapter.py 暴露三个函数）
FAKE_ADAPTER = '''
def detect(directory):
    return {"engine_id": "fake", "display_name": "Fake", "confidence": 0.9,
            "version": "1.0", "details": {}}

def extract(source_path):
    return [{"locator": "$.items[0].name", "source": "こんにちは",
             "context_json": '{"file_path": "Items.json", "char_ranges": [[0, 12]]}'}]

def write_back(source_path, output_dir, entries):
    return {"output_dir": output_dir, "written_count": len(entries), "warning_count": 0}
'''


@pytest.fixture()
def plugin_dir(tmp_path):
    """一个合法假插件目录 plugins/fake/（plugin.json + adapter.py）。"""
    d = tmp_path / "plugins" / "fake"
    d.mkdir(parents=True)
    (d / "plugin.json").write_text(json.dumps({
        "engine_id": "fake", "display_name": "Fake Engine", "api_version": PLUGIN_API_VERSION,
        "version": "0.1.0", "entry": "adapter.py", "author": "test",
    }), encoding="utf-8")
    (d / "adapter.py").write_text(FAKE_ADAPTER, encoding="utf-8")
    return d


@pytest.fixture()
def manager(plugin_dir):
    return PluginManager([str(plugin_dir.parent)])


@pytest.fixture()
def reg():
    return register_core_methods()


@pytest.fixture()
def ctx():
    return {}


def _rpc(reg, ctx, method: str, params=None, rid: int = 1) -> dict | None:
    req = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        req["params"] = params
    return _process_line(json.dumps(req, ensure_ascii=False), reg, ctx)


# ---------- PluginManager 加载契约 ----------

def test_loads_valid_plugin(plugin_dir):
    mgr = PluginManager([str(plugin_dir.parent)])
    infos = mgr.infos()
    assert len(infos) == 1
    info = infos[0]
    assert info.engine_id == "fake" and info.loaded is True and info.error is None
    assert callable(mgr.get_entry("fake").extract)


def test_bad_api_version_disabled(tmp_path):
    d = tmp_path / "plugins" / "old"
    d.mkdir(parents=True)
    (d / "plugin.json").write_text(json.dumps({
        "engine_id": "old", "display_name": "Old", "api_version": "0.9",
        "version": "0.1.0", "entry": "adapter.py",
    }), encoding="utf-8")
    (d / "adapter.py").write_text(FAKE_ADAPTER, encoding="utf-8")
    mgr = PluginManager([str(d.parent)])
    info = mgr.infos()[0]
    assert info.loaded is False and "api_version" in (info.error or "")


def test_missing_callable_disabled(tmp_path):
    d = tmp_path / "plugins" / "broken"
    d.mkdir(parents=True)
    (d / "plugin.json").write_text(json.dumps({
        "engine_id": "broken", "display_name": "Broken", "api_version": PLUGIN_API_VERSION,
        "version": "0.1.0", "entry": "adapter.py",
    }), encoding="utf-8")
    (d / "adapter.py").write_text("def detect(d): return {}  # 缺 extract/write_back\n", encoding="utf-8")
    mgr = PluginManager([str(d.parent)])
    info = mgr.infos()[0]
    assert info.loaded is False and "extract" in (info.error or "")


def test_bad_manifest_recorded(tmp_path):
    d = tmp_path / "plugins" / "junk"
    d.mkdir(parents=True)
    (d / "plugin.json").write_text("not json", encoding="utf-8")
    mgr = PluginManager([str(d.parent)])
    info = mgr.infos()[0]
    assert info.loaded is False and "manifest" in (info.error or "")


def test_get_raises_for_missing(manager):
    from gt_core.rpc.errors import RpcError
    with pytest.raises(RpcError) as ei:
        manager.get("nope")
    assert ei.value.code == RpcErrorCode.ENGINE_NOT_SUPPORTED


def test_get_raises_for_disabled():
    from gt_core.rpc.errors import RpcError
    mgr = PluginManager([])
    with pytest.raises(RpcError) as ei:
        mgr.get("fake")
    assert ei.value.code == RpcErrorCode.ENGINE_NOT_SUPPORTED


def test_api_version_1_1_compatible(tmp_path):
    """api_version 1.1（加性演进）同 major 兼容加载；0.9 / 2.0 拒绝。"""
    d = tmp_path / "plugins" / "v11"
    d.mkdir(parents=True)
    (d / "plugin.json").write_text(json.dumps({
        "engine_id": "v11", "display_name": "V11", "api_version": "1.1",
        "version": "0.1.0", "entry": "adapter.py",
    }), encoding="utf-8")
    (d / "adapter.py").write_text(FAKE_ADAPTER, encoding="utf-8")
    mgr = PluginManager([str(d.parent)])
    assert mgr.infos()[0].loaded is True

    # 2.0（不同 major）拒绝（同目录树扫到 v11，按 engine_id 过滤取 v20）
    d2 = tmp_path / "plugins" / "v20"
    d2.mkdir(parents=True)
    (d2 / "plugin.json").write_text(json.dumps({
        "engine_id": "v20", "display_name": "V20", "api_version": "2.0",
        "version": "0.1.0", "entry": "adapter.py",
    }), encoding="utf-8")
    (d2 / "adapter.py").write_text(FAKE_ADAPTER, encoding="utf-8")
    mgr2 = PluginManager([str(d2.parent)])
    v20 = next(i for i in mgr2.infos() if i.engine_id == "v20")
    assert v20.loaded is False


def test_get_protector_feature_detect(tmp_path):
    """插件提供 protect/restore → get_protector 返回；缺省 → None。"""
    d = tmp_path / "plugins" / "fake"
    d.mkdir(parents=True)
    (d / "plugin.json").write_text(json.dumps({
        "engine_id": "fake", "display_name": "Fake", "api_version": PLUGIN_API_VERSION,
        "version": "0.1.0", "entry": "adapter.py",
    }), encoding="utf-8")
    # 带 protect/restore 的插件
    (d / "adapter.py").write_text(FAKE_ADAPTER + '''
def protect(text):
    return text, []
def restore(text, tokens):
    return text
def has_protected(text):
    return False
''', encoding="utf-8")
    mgr = PluginManager([str(d.parent)])
    prot = mgr.get_protector("fake")
    assert prot is not None
    p, r, h = prot
    assert callable(p) and callable(r) and callable(h)
    assert "protect" in mgr.infos()[0].features

    # 无 protect 的插件 → None（get_protector 对缺失能力降级，不抛）
    d2 = tmp_path / "other" / "plugins" / "fake2"
    d2.mkdir(parents=True)
    (d2 / "plugin.json").write_text(json.dumps({
        "engine_id": "fake2", "display_name": "Fake2", "api_version": PLUGIN_API_VERSION,
        "version": "0.1.0", "entry": "adapter.py",
    }), encoding="utf-8")
    (d2 / "adapter.py").write_text(FAKE_ADAPTER, encoding="utf-8")
    mgr2 = PluginManager([str(d2.parent)])
    assert mgr2.get_protector("fake2") is None
    assert "protect" not in mgr2.infos()[0].features


# ---------- RPC：plugins.list / detect.run ----------

def test_plugins_list_rpc(reg, ctx, plugin_dir):
    methods._set_plugin_manager(PluginManager([str(plugin_dir.parent)]))
    try:
        resp = _rpc(reg, ctx, "plugins.list")
        assert resp["result"][0]["engine_id"] == "fake"
        assert resp["result"][0]["loaded"] is True
    finally:
        methods._set_plugin_manager(None)


def test_detect_run_recognizes(reg, ctx, plugin_dir):
    methods._set_plugin_manager(PluginManager([str(plugin_dir.parent)]))
    try:
        resp = _rpc(reg, ctx, "detect.run", {"dir": str(plugin_dir)})
        assert resp["result"]["engine_id"] == "fake"
        assert resp["result"]["confidence"] == 0.9
    finally:
        methods._set_plugin_manager(None)


def test_detect_run_no_plugin(reg, ctx):
    methods._set_plugin_manager(PluginManager([]))
    try:
        resp = _rpc(reg, ctx, "detect.run", {"dir": "C:/whatever"})
        assert resp["error"]["code"] == RpcErrorCode.ENGINE_NOT_SUPPORTED
    finally:
        methods._set_plugin_manager(None)


def test_detect_run_requires_dir_param(reg, ctx):
    resp = _rpc(reg, ctx, "detect.run")
    assert resp["error"]["code"] == RpcErrorCode.INVALID_PARAMS


def test_extract_requires_project(reg, ctx, plugin_dir):
    methods._set_plugin_manager(PluginManager([str(plugin_dir.parent)]))
    try:
        resp = _rpc(reg, ctx, "extract.run")
        assert resp["error"]["code"] == RpcErrorCode.NO_PROJECT
    finally:
        methods._set_plugin_manager(None)


# ---------- RPC：extract.run / write_back.run 端到端（假插件） ----------

@pytest.fixture()
def project(reg, ctx, tmp_path):
    """创建 rpgmv 项目（假插件 engine_id=fake，故这里用 fake 引擎）。"""
    path = str(tmp_path / "proj.sqlite3")
    src = str(tmp_path / "game")
    Path(src).mkdir(parents=True)
    resp = _rpc(reg, ctx, "project.create",
                {"path": path, "engine_id": "fake", "source_path": src}, rid=1)
    assert resp and resp.get("result"), resp
    return ctx["project"]


def test_extract_then_writeback_flow(reg, ctx, plugin_dir, project):
    methods._set_plugin_manager(PluginManager([str(plugin_dir.parent)]))
    try:
        resp = _rpc(reg, ctx, "extract.run", {}, rid=2)
        assert resp["result"]["extracted_count"] == 1
        assert resp["result"]["engine_id"] == "fake"
        # 项目状态推进：created -> detecting -> extracted
        assert project.get_state().value == "extracted"
        # 条目落库，locator/context 都保留
        page = _rpc(reg, ctx, "entries.list", {"page": 1, "page_size": 10}, rid=3)
        e = page["result"]["items"][0]
        assert e["source"] == "こんにちは"
        assert "char_ranges" in json.loads(e["context_json"])

        # 给条目填译文
        upd = _rpc(reg, ctx, "entries.update",
                   {"id": e["id"], "translation": "你好", "status": 2}, rid=4)
        assert upd["result"]["translation"] == "你好"

        # write_back：fake 插件计数
        out = str(project.path.parent / "out")
        wb = _rpc(reg, ctx, "write_back.run", {"output_dir": out}, rid=5)
        assert wb["result"]["written_count"] == 1
        assert project.get_state().value == "done"
    finally:
        methods._set_plugin_manager(None)


def test_writeback_no_translation(reg, ctx, plugin_dir, project):
    """没翻译任何条目时 write_back 不崩、written_count=0。"""
    methods._set_plugin_manager(PluginManager([str(plugin_dir.parent)]))
    try:
        _rpc(reg, ctx, "extract.run", {}, rid=2)
        out = str(project.path.parent / "out")
        wb = _rpc(reg, ctx, "write_back.run", {"output_dir": out}, rid=3)
        assert wb["result"]["written_count"] == 0
        assert project.get_state().value == "done"
    finally:
        methods._set_plugin_manager(None)


def test_writeback_requires_output_dir(reg, ctx, project):
    resp = _rpc(reg, ctx, "write_back.run", {})
    assert resp["error"]["code"] == RpcErrorCode.INVALID_PARAMS
