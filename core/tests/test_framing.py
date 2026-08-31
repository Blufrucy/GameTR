"""serve_stdio 集成测试：NDJSON 帧、UTF-8、EOF 退出、RPC 全量日志。"""

import io
import json

from gt_core.rpc.methods import register_core_methods
from gt_core.rpc.server import serve_stdio


def _run(lines: list[str], *, log_dir=None) -> str:
    stdin = io.StringIO("\n".join(lines) + "\n")
    stdout = io.StringIO()
    serve_stdio(register_core_methods(), stdin=stdin, stdout=stdout, rpc_log_dir=log_dir)
    return stdout.getvalue()


def _parse(out: str) -> list[dict]:
    return [json.loads(line) for line in out.strip().splitlines()]


def test_ndjson_one_message_per_line():
    out = _run(
        [
            '{"jsonrpc":"2.0","id":1,"method":"core.ping"}',
            '{"jsonrpc":"2.0","id":2,"method":"core.ping"}',
        ]
    )
    msgs = _parse(out)
    assert [m["id"] for m in msgs] == [1, 2]
    assert all(m["result"]["pong"] is True for m in msgs)


def test_blank_lines_skipped_and_eof_exits():
    out = _run(["", '{"jsonrpc":"2.0","id":1,"method":"core.ping"}', ""])
    assert len(_parse(out)) == 1


def test_utf8_chinese_roundtrip():
    reg = register_core_methods()

    @reg.register("test.echo")
    def echo(params, ctx):
        return params

    stdin = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"test.echo","params":{"文本":"勇者の剣"}}\n')
    stdout = io.StringIO()
    serve_stdio(reg, stdin=stdin, stdout=stdout)
    resp = json.loads(stdout.getvalue())
    # 编码/转义不能破坏非 ASCII 文本
    assert resp["result"]["文本"] == "勇者の剣"


def test_notification_produces_no_output():
    out = _run(['{"jsonrpc":"2.0","method":"core.ping"}'])
    assert out.strip() == ""


def test_notification_stream_keeps_framing_atomic():
    """M3 通知流：服务端主动推送（无 id 通知）不得与响应行交错破坏 NDJSON 帧。

    每条输出行都必须独立可解析；响应仍按 id 配对；通知存在且无错误。
    """
    reg = register_core_methods()

    @reg.register("test.notify")
    def notify(params, ctx):
        # 后台任务同款写法：经 ctx['notify'] 推送通知（put_nowait 进单 writer 队列）
        ctx["notify"]({"jsonrpc": "2.0", "method": "progress", "params": {"done": 1, "total": 2}})
        ctx["notify"]({"jsonrpc": "2.0", "method": "progress", "params": {"done": 2, "total": 2}})
        return {"ok": True}

    stdin = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"test.notify"}\n')
    stdout = io.StringIO()
    serve_stdio(reg, stdin=stdin, stdout=stdout)
    msgs = [json.loads(line) for line in stdout.getvalue().strip().splitlines()]
    # 每行独立可解析（上面 json.loads 已保证）+ 行数 = 2 通知 + 1 响应
    assert len(msgs) == 3
    progress = [m for m in msgs if m.get("method") == "progress"]
    assert len(progress) == 2
    assert [p["params"]["done"] for p in progress] == [1, 2]
    resp = [m for m in msgs if m.get("id") == 1]
    assert len(resp) == 1 and resp[0]["result"] == {"ok": True}
    assert all("error" not in m for m in msgs)


def test_background_task_notifications_flushed_after_eof():
    """EOF 顺序修复：后台任务（async handler create_task）的通知必须在 EOF 后写出。

    曾 bug：先关 writer 再等后台任务 → 通知堆积丢失（stderr 有 progress、stdout 无），
    前端收不到 done 不刷新（用户「翻译出错」根因）。
    """
    import asyncio

    reg = register_core_methods()

    @reg.register("test.bg_notify")
    async def bg_notify(params, ctx):
        async def task():
            await asyncio.sleep(0.01)
            ctx["notify"]({"jsonrpc": "2.0", "method": "progress",
                           "params": {"task_id": "t1", "done": 5, "total": 5, "status": "done"}})
        asyncio.get_running_loop().create_task(task())
        return {"ok": True}

    stdin = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"test.bg_notify"}\n')
    stdout = io.StringIO()
    serve_stdio(reg, stdin=stdin, stdout=stdout)
    msgs = [json.loads(line) for line in stdout.getvalue().strip().splitlines()]
    progress = [m for m in msgs if m.get("method") == "progress"]
    assert len(progress) == 1, f"后台任务通知应在 EOF 后写出: {msgs}"
    assert progress[0]["params"]["status"] == "done"


def test_rpc_log_records_req_and_resp(tmp_path):
    _run(['{"jsonrpc":"2.0","id":1,"method":"core.ping"}'], log_dir=tmp_path)
    logs = list(tmp_path.glob("rpc-*.ndjson"))
    assert len(logs) == 1
    content = logs[0].read_text(encoding="utf-8")
    assert '"t": "req"' in content
    assert '"t": "resp"' in content


def test_rpc_log_masks_sensitive_fields(tmp_path):
    """api_key/token 等敏感字段必须脱敏后落盘（防密钥进排障日志）。"""
    from gt_core.rpc.server import mask_sensitive

    # 单测掩码函数：参数里的 api_key、嵌套 token、非 JSON 字符串兜底
    assert mask_sensitive({"params": {"api_key": "sk-abcdef123456", "text": "你好"}}) == {
        "params": {"api_key": "***", "text": "你好"}
    }
    assert mask_sensitive({"nested": {"access_token": "tok_xyz"}}) == {
        "nested": {"access_token": "***"}
    }
    assert mask_sensitive({"cache_key": "keep-me"}) == {"cache_key": "keep-me"}  # 不误伤
    assert mask_sensitive("Authorization: Bearer sk-longsecretvalue") == \
        "Authorization: Bearer sk-long***"

    # 端到端：带 api_key 的请求行落盘后不含明文（日志行内 line 是转义 JSON，解析后断言）
    _run([
        '{"jsonrpc":"2.0","id":1,"method":"test.echo",'
        '"params":{"api_key":"sk-supersecretvalue","text":"hello"}}'
    ], log_dir=tmp_path)
    logs = list(tmp_path.glob("rpc-*.ndjson"))
    content = logs[0].read_text(encoding="utf-8")
    assert "sk-supersecretvalue" not in content
    records = [json.loads(line) for line in content.splitlines()]
    req = json.loads(records[0]["line"])  # {"jsonrpc":..., "params":{"api_key":...}}
    assert req["params"]["api_key"] == "***"
    assert req["params"]["text"] == "hello"  # 非敏感字段原样保留
