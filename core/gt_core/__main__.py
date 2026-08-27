"""gt-core CLI 入口。

用法：
  gt-core serve       # stdio JSON-RPC 模式（默认，供 Tauri GUI 使用）
  gt-core self-test   # headless 自检（CI 用；M2 起扩展为全流程）

进程生命周期由 Rust 壳管理（spawn / kill / 心跳重启，见 ADR-0001）。
"""

from __future__ import annotations

import sys

from gt_core.rpc.methods import register_core_methods
from gt_core.rpc.server import _process_line, serve_stdio


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "self-test":
        run_self_test()
        return
    # serve 是默认模式：不带参数也进 serve，方便 Tauri externalBin 直接拉起
    serve_stdio(register_core_methods())


def run_self_test() -> None:
    """M0 自检：进程内跑一个 core.ping 往返，验证协议栈与核心可加载。"""
    reg = register_core_methods()
    resp = _process_line('{"jsonrpc":"2.0","id":1,"method":"core.ping"}', reg, {})
    result = resp.get("result", {}) if resp else {}
    if not (isinstance(result, dict) and result.get("pong") is True):
        print(f"gt-core self-test: FAIL {resp!r}", file=sys.stderr)
        sys.exit(1)
    print(f"gt-core self-test: OK (pid={result.get('pid')}, version={result.get('version')})")


if __name__ == "__main__":
    main()
