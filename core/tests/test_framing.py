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


def test_rpc_log_records_req_and_resp(tmp_path):
    _run(['{"jsonrpc":"2.0","id":1,"method":"core.ping"}'], log_dir=tmp_path)
    logs = list(tmp_path.glob("rpc-*.ndjson"))
    assert len(logs) == 1
    content = logs[0].read_text(encoding="utf-8")
    assert '"t": "req"' in content
    assert '"t": "resp"' in content
