"""Provider 层单测（M3）：Mock 确定性、三形态解析、ProviderManager、RPC、吞吐。"""

from __future__ import annotations

import asyncio
import io
import json
import time

from gt_core.providers.base import TranslateItem
from gt_core.providers.manager import ProviderManager
from gt_core.providers.mock import MockProvider
from gt_core.providers.openai_compat import OpenAICompatibleProvider
from gt_core.rpc.methods import _set_provider_manager, register_core_methods
from gt_core.rpc.server import serve_stdio


def test_mock_deterministic_and_keeps_placeholders():
    p = MockProvider()
    batch = [TranslateItem(id="a", text="こんにちは⟦0⟧勇者⟦1⟧！"),
             TranslateItem(id="b", text="はい")]
    r1 = asyncio.run(p.translate_batch(batch))
    r2 = asyncio.run(p.translate_batch(batch))
    # 确定性：同输入两次输出一致
    assert [(r.id, r.translation) for r in r1] == [(r.id, r.translation) for r in r2]
    # 占位符保留（流水线 Validator 依赖）
    assert "⟦0⟧" in r1[0].translation and "⟦1⟧" in r1[0].translation


def test_parse_translations_three_forms():
    """spike4 实测：模型跟随 user 输入结构当模板，三种响应形态都要能解析。"""
    forms = [
        # 1. 协议标准形态
        '{"translations": [{"id": "n0", "translation": "你好"}]}',
        # 2. 模型跟随 user 的 items 模板
        '{"items": [{"id": "n0", "text": "你好"}]}',
        # 3. 顶层数组
        '[{"id": "n0", "translation": "你好"}]',
    ]
    for content in forms:
        parsed = OpenAICompatibleProvider._parse_translations(content)
        assert parsed == [{"id": "n0", "translation": "你好"}]


def test_payload_structured_json_schema():
    """最新 API：structured 用 json_schema strict（官方推荐），否则 json_object。"""
    p = OpenAICompatibleProvider()
    batch = [TranslateItem(id="a", text="こんにちは")]
    # structured=True → json_schema + strict + schema
    body = p._payload(batch, "gpt-5", structured=True)
    rf = body["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["name"] == "translations"
    assert rf["json_schema"]["schema"]["required"] == ["translations"]
    # structured=False → json_object（兼容降级）
    body2 = p._payload(batch, "gpt-5", structured=False)
    assert body2["response_format"] == {"type": "json_object"}
    # max_tokens 保留（第三方兼容端点通用）
    assert body["max_tokens"] == 4096


def test_translate_batch_falls_back_to_json_object(monkeypatch):
    """端点不支持 json_schema（400）→ 自动降级 json_object 重试。"""
    p = OpenAICompatibleProvider()
    calls = []

    async def fake_post(payload, api_key):
        calls.append(payload["response_format"]["type"])
        if payload["response_format"]["type"] == "json_schema":
            from gt_core.providers.openai_compat import StructuredUnsupportedError
            raise StructuredUnsupportedError()
        return {
            "choices": [{"message": {"content": '{"translations": [{"id": "a", "translation": "你好"}]}'}}],
            "usage": {},
        }

    monkeypatch.setattr(p, "_post", fake_post)

    import asyncio

    async def run():
        return await p.translate_batch(
            [TranslateItem(id="a", text="こんにちは")], model="m", api_key="sk-x"
        )

    r = asyncio.run(run())
    # 先 json_schema 失败，再 json_object 成功
    assert calls == ["json_schema", "json_object"]
    assert r[0].translation == "你好"


def _set_connect(provider, *, error=None):
    """给 provider 注入假 _connect（连通性 ping seam）：默认立即握手成功，error 指定抛错。"""
    async def fake_connect(host, port, use_tls):
        if error is not None:
            raise error
    provider._connect = fake_connect  # type: ignore[method-assign]  # 测试注入


def test_ping_success_fast():
    """连通性自检 = TCP/TLS ping：握手成功即 ok，毫秒级——不发 HTTP、不验 key。

    （旧实现先发 chat 生成、后发 GET /models，都等慢服务端响应 → 实测 ~9s；ping 语义下无此拖累）
    """
    p = OpenAICompatibleProvider()
    _set_connect(p)
    ok, latency, msg = asyncio.run(p.test(api_key="sk-x"))
    assert ok is True
    assert latency < 1000  # 纯握手，本地无网络往返
    assert msg == "ok"


def test_ping_timeout_reports_host():
    """握手超时（_PING_TIMEOUT 上限）→ 提示连接超时 + host:port，秒级见分晓。"""
    p = OpenAICompatibleProvider(base_url="https://unreachable.example/v1")
    _set_connect(p, error=TimeoutError())
    ok, _, msg = asyncio.run(p.test())
    assert ok is False
    assert "超时" in msg and "unreachable.example" in msg


def test_ping_conn_error_reports_host():
    """拒连/DNS 失败（OSError 族，含 ConnectionRefusedError）→ 提示无法连接 host:port。"""
    p = OpenAICompatibleProvider(base_url="https://unreachable.example/v1")
    _set_connect(p, error=ConnectionRefusedError("conn refused"))
    ok, _, msg = asyncio.run(p.test())
    assert ok is False
    assert "无法连接" in msg and "unreachable.example" in msg


def test_ping_bad_url_reports_invalid():
    """地址缺协议/非 http(s) → 无需联网即提示地址无效（_endpoint 解析失败）。"""
    p = OpenAICompatibleProvider(base_url="api.deepseek.com")  # 缺 https://
    ok, _, msg = asyncio.run(p.test())
    assert ok is False and "无效" in msg


def test_models_classifies_auth_balance(monkeypatch):
    """key/余额问题在「拉模型列表」暴露（ping 不验 key）：401/402/403 分类人话。"""
    p = OpenAICompatibleProvider()
    for status, keyword in ((401, "无效"), (402, "余额"), (403, "无权限")):
        async def fake_get_json(url, headers, _status=status):
            return _status, None, ""
        monkeypatch.setattr(p, "_http_get_json", fake_get_json)
        try:
            asyncio.run(p.list_models("sk-x"))
            raise AssertionError("应抛 RuntimeError")
        except RuntimeError as exc:
            assert keyword in str(exc)


def test_models_404_guides_base_url(monkeypatch):
    """404 → 提示 base_url 常见错误（缺 /v1），帮用户改对地址。"""
    async def fake_get_json(url, headers):
        return 404, None, ""
    p = OpenAICompatibleProvider(base_url="https://api.openai.com")
    monkeypatch.setattr(p, "_http_get_json", fake_get_json)
    try:
        asyncio.run(p.list_models("sk-x"))
        raise AssertionError("应抛 RuntimeError")
    except RuntimeError as exc:
        assert "/v1" in str(exc)


def test_list_models_allows_no_key(monkeypatch):
    """list_models 支持无 key（本地端点）：空 key 不报错、不带 Authorization 头。"""
    import asyncio
    sent_headers = {}
    async def fake_get_json(url, headers):
        sent_headers.update(headers)
        return 200, {"data": [{"id": "qwen2.5"}]}, ""
    p = OpenAICompatibleProvider()
    monkeypatch.setattr(p, "_http_get_json", fake_get_json)
    assert asyncio.run(p.list_models()) == ["qwen2.5"]
    assert "Authorization" not in sent_headers  # 本地端点不带 key


def test_provider_manager_no_builtin(monkeypatch, tmp_path):
    """无内置 Provider（删 mock/openai 自动注册）：空配置 → 空列表，用户配置驱动。"""
    import gt_core.providers.manager as pm

    monkeypatch.setattr(pm, "_CONFIG_FILE", tmp_path / "providers.json")
    mgr = ProviderManager()
    assert mgr.infos() == []
    # configure 后出现
    mgr.configure(provider_id="deepseek", base_url="https://api.deepseek.com", models=["deepseek-v4-flash"])
    assert [p.provider_id for p in mgr.infos()] == ["deepseek"]


def test_provider_configure_persists(monkeypatch, tmp_path):
    """providers.configure：接入真实 Provider（DeepSeek），持久化 + 重载生效 + key 解析。"""
    import gt_core.providers.manager as pm

    monkeypatch.setattr(pm, "_CONFIG_FILE", tmp_path / "providers.json")
    mgr = ProviderManager()
    info = mgr.configure(
        provider_id="deepseek", base_url="https://api.deepseek.com",
        display_name="DeepSeek", models=["deepseek-v4-flash"], api_key="sk-test123",
    )
    assert info.base_url == "https://api.deepseek.com"
    assert info.models == ["deepseek-v4-flash"]
    assert "deepseek" in {p.provider_id for p in mgr.infos()}
    assert mgr.resolve_api_key("deepseek") == "sk-test123"

    # 新实例重载配置（持久化生效）
    mgr2 = ProviderManager()
    ids = {p.provider_id for p in mgr2.infos()}
    assert "deepseek" in ids
    ds = next(p for p in mgr2.infos() if p.provider_id == "deepseek")
    assert ds.base_url == "https://api.deepseek.com"
    assert mgr2.resolve_api_key("deepseek") == "sk-test123"


def test_provider_remove_clears_config_and_key(monkeypatch, tmp_path):
    """providers.remove：删除 Provider（内存 + 持久化），key 一并清除；幂等。"""
    import gt_core.providers.manager as pm
    monkeypatch.setattr(pm, "_CONFIG_FILE", tmp_path / "providers.json")
    mgr = ProviderManager()
    mgr.configure(provider_id="deepseek", base_url="https://api.deepseek.com", api_key="sk-x")
    assert mgr.remove("deepseek") is True
    assert mgr.infos() == []
    assert mgr.resolve_api_key("deepseek") is None  # key 缓存也清掉
    assert mgr.remove("deepseek") is False  # 幂等：再删返回 False
    # 持久化生效：新实例读不到
    assert ProviderManager().infos() == []


def test_providers_remove_rpc(monkeypatch, tmp_path):
    """providers.remove RPC：删除后 providers.list 空；不存在幂等 removed=false。"""
    import gt_core.providers.manager as pm
    monkeypatch.setattr(pm, "_CONFIG_FILE", tmp_path / "providers.json")
    mgr = ProviderManager()
    mgr.configure(provider_id="deepseek", base_url="https://api.deepseek.com", api_key="sk-x")
    _set_provider_manager(mgr)
    try:
        reg = register_core_methods()
        stdin = io.StringIO(
            '{"jsonrpc":"2.0","id":1,"method":"providers.remove",'
            '"params":{"provider_id":"deepseek"}}\n'
        )
        stdout = io.StringIO()
        serve_stdio(reg, stdin=stdin, stdout=stdout)
        resp = json.loads(stdout.getvalue())
        assert resp["result"] == {"provider_id": "deepseek", "removed": True}
    finally:
        _set_provider_manager(None)


def _mgr_with_mock() -> ProviderManager:
    """测试注入：空 ProviderManager + MockProvider（产品不含 mock，测试用）。"""
    mgr = ProviderManager()
    mgr._providers["mock"] = MockProvider()  # type: ignore[attr-defined]  # 测试注入
    return mgr


def test_providers_list_rpc(monkeypatch, tmp_path):
    import gt_core.providers.manager as pm
    monkeypatch.setattr(pm, "_CONFIG_FILE", tmp_path / "providers.json")  # 隔离用户真实配置
    _set_provider_manager(ProviderManager())
    try:
        reg = register_core_methods()
        stdin = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"providers.list"}\n')
        stdout = io.StringIO()
        serve_stdio(reg, stdin=stdin, stdout=stdout)
        resp = json.loads(stdout.getvalue())
        assert resp["result"] == []  # 无内置：未配置时列表为空
    finally:
        _set_provider_manager(None)


def test_providers_test_mock_rpc():
    """providers.test 走 async handler（serve_loop await），mock 返回 ok。"""
    _set_provider_manager(_mgr_with_mock())
    try:
        reg = register_core_methods()
        stdin = io.StringIO(
            '{"jsonrpc":"2.0","id":1,"method":"providers.test",'
            '"params":{"provider_id":"mock"}}\n'
        )
        stdout = io.StringIO()
        serve_stdio(reg, stdin=stdin, stdout=stdout)
        resp = json.loads(stdout.getvalue())
        assert resp["result"]["ok"] is True
        assert resp["result"]["provider_id"] == "mock"
    finally:
        _set_provider_manager(None)


def test_providers_test_unknown():
    _set_provider_manager(ProviderManager())
    try:
        reg = register_core_methods()
        stdin = io.StringIO(
            '{"jsonrpc":"2.0","id":1,"method":"providers.test",'
            '"params":{"provider_id":"nope"}}\n'
        )
        stdout = io.StringIO()
        serve_stdio(reg, stdin=stdin, stdout=stdout)
        resp = json.loads(stdout.getvalue())
        assert resp["error"]["code"] == -32004  # PROVIDER_ERROR
    finally:
        _set_provider_manager(None)


def test_mock_5000_batch_under_30s():
    """M3 验收：Mock Provider 5000 条 <30s（async 整批无 HTTP 开销，应远小于 30s）。"""
    p = MockProvider()
    batch = [TranslateItem(id=f"e{i}", text=f"テキスト{i}番目") for i in range(5000)]
    t0 = time.monotonic()
    results = asyncio.run(p.translate_batch(batch))
    elapsed = time.monotonic() - t0
    assert len(results) == 5000
    assert elapsed < 30.0, f"Mock 5000 条耗时 {elapsed:.1f}s 超验收线"
