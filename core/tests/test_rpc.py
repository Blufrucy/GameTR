"""RPC 分发层单测：_process_line 纯函数行为（协议正确性的最小保证）。"""

import json

import pytest

from gt_core.rpc.errors import RpcErrorCode
from gt_core.rpc.methods import register_core_methods
from gt_core.rpc.server import _process_line


@pytest.fixture()
def registry():
    return register_core_methods()


def _call(line: str, reg) -> dict | None:
    return _process_line(line, reg, {})


def test_ping_returns_pong(registry):
    resp = _call('{"jsonrpc":"2.0","id":1,"method":"core.ping"}', registry)
    assert resp["id"] == 1
    result = resp["result"]
    assert result["pong"] is True
    assert "version" in result
    assert "pid" in result
    assert isinstance(result["ts"], (int, float))


def test_unknown_method_returns_error(registry):
    resp = _call('{"jsonrpc":"2.0","id":2,"method":"nope"}', registry)
    assert resp["id"] == 2
    assert resp["error"]["code"] == RpcErrorCode.METHOD_NOT_FOUND
    assert "nope" in resp["error"]["message"]


def test_notification_gets_no_response(registry):
    # 无 id 的请求是通知，不应答（否则 GUI 的并发通知会打乱响应流）
    assert _call('{"jsonrpc":"2.0","method":"core.ping"}', registry) is None


def test_parse_error():
    resp = _call("{not json", register_core_methods())
    assert resp["id"] is None
    assert resp["error"]["code"] == RpcErrorCode.PARSE_ERROR


def test_invalid_request_missing_method():
    resp = _call('{"jsonrpc":"2.0","id":3}', register_core_methods())
    assert resp["error"]["code"] == RpcErrorCode.INVALID_REQUEST


def test_invalid_params_must_be_object(registry):
    resp = _call('{"jsonrpc":"2.0","id":4,"method":"core.log_level","params":"x"}', registry)
    assert resp["error"]["code"] == RpcErrorCode.INVALID_PARAMS


def test_log_level_roundtrip(registry):
    # ctx 在真实 serve 中跨请求共享（同一次会话）；这里显式共享同一个 dict
    ctx: dict = {}
    _process_line('{"jsonrpc":"2.0","id":1,"method":"core.log_level","params":{"level":"debug"}}', registry, ctx)
    resp = _process_line('{"jsonrpc":"2.0","id":2,"method":"core.log_level"}', registry, ctx)
    assert resp["result"]["level"] == "debug"


def test_log_level_rejects_unknown(registry):
    resp = _call('{"jsonrpc":"2.0","id":1,"method":"core.log_level","params":{"level":"nope"}}', registry)
    assert resp["error"]["code"] == RpcErrorCode.INVALID_PARAMS


def test_shutdown_sets_stopped(registry):
    resp = _call('{"jsonrpc":"2.0","id":9,"method":"core.shutdown"}', registry)
    assert resp["result"]["ok"] is True
    assert registry.stopped is True


def test_error_uses_jsonrpc_2_0_and_id():
    resp = _call('{"jsonrpc":"2.0","id":7,"method":"core.ping"}', register_core_methods())
    assert set(resp.keys()) == {"jsonrpc", "id", "result"}
    assert json.loads(json.dumps(resp)) == resp  # 序列化往返不破坏结构
