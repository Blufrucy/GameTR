"""gt-core CLI 入口。

用法：
  gt-core serve       # stdio JSON-RPC 模式（默认，供 Tauri GUI 使用）
  gt-core self-test   # headless 全流程自检（CI 用）：建项目→插数据→查询→搜索→重开

进程生命周期由 Rust 壳管理（spawn / kill / 心跳重启，见 ADR-0001）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from gt_core.ir import entry_id
from gt_core.project import Project
from gt_core.rpc.methods import register_core_methods
from gt_core.rpc.models import Entry, EntryStatus
from gt_core.rpc.server import _process_line, serve_stdio

# self-test 插入条数（验收场景 10 万条在 tests/test_perf.py 覆盖，这里保持轻量快速）
_SELF_TEST_ROWS = 5_000


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "self-test":
        run_self_test()
        return
    # serve 是默认模式：不带参数也进 serve，方便 Tauri externalBin 直接拉起
    serve_stdio(register_core_methods())


def run_self_test() -> None:
    """M1 自检：协议栈 + 项目全流程（建库/插入/分页/搜索/重开）。

    模拟 M1 验收脚本的每个环节，任一步失败即退出码 1。
    """
    steps: list[tuple[str, bool]] = []

    def step(name: str) -> None:
        steps.append((name, True))

    # 0) 协议栈
    reg = register_core_methods()
    resp = _process_line('{"jsonrpc":"2.0","id":1,"method":"core.ping"}', reg, {})
    result = resp.get("result", {}) if resp else {}
    if not (isinstance(result, dict) and result.get("pong") is True):
        print(f"gt-core self-test: FAIL ping {resp!r}", file=sys.stderr)
        sys.exit(1)
    step("core.ping")

    # 1) 创建项目
    # ignore_cleanup_errors：清理失败（如 Windows 句柄占用）不掩盖真实断言错误
    with tempfile.TemporaryDirectory(prefix="gt-core-self-test-", ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "proj.gametr.sqlite3"
        project = Project.create(db_path, engine_id="rpgmv", source_path="/fake/game")
        step(f"project.create -> {db_path.name}")

        # 2) 批量插入（模拟 10 万条的缩小版）
        entries = [
            Entry(
                id=entry_id("rpgmv", f"loc:{i}", source),
                source=source,
                translation=None,
                status=EntryStatus.PENDING,
                locator=f"loc:{i}",
                context_json='{"file_path": "Map001.json"}',
                updated_at=0.0,
            )
            for i, source in enumerate(f"sample line {i} 你好勇者" for i in range(_SELF_TEST_ROWS))
        ]
        n = project.repo.upsert_entries(entries)
        assert n == _SELF_TEST_ROWS, f"插入条数不符: {n}"
        step(f"insert {_SELF_TEST_ROWS} entries")

        # 3) 分页查询 + 状态过滤
        page = project.repo.list_entries(page=2, page_size=100, status=EntryStatus.PENDING)
        assert page.total == _SELF_TEST_ROWS and len(page.items) == 100
        step(f"paged query (page 2, {len(page.items)} items, total {page.total})")

        # 4) 改状态（批量重翻语义：确认后不再被批量改动）
        ids = [e.id for e in page.items]
        project.repo.batch_update_status(ids, EntryStatus.MACHINE)
        assert project.repo.stats().by_status[str(int(EntryStatus.MACHINE))] >= 100
        step(f"batch_update_status {len(ids)} -> MACHINE")

        # 5) FTS 搜索
        hits = project.repo.search("勇者", page_size=20)
        assert hits.total > 0, "FTS 搜索无结果"
        step(f"fts search '勇者' -> {hits.total} hits")

        # 6) 保存重开（关闭连接 -> 重新 open -> 数据仍在）
        project.close()
        reopened = Project.open(db_path)
        assert reopened.repo.count() == _SELF_TEST_ROWS
        step(f"reopen ({db_path.name}, {reopened.repo.count()} entries preserved)")
        reopened.close()

    ok = all(ok for _, ok in steps)
    print(f"gt-core self-test: {'OK' if ok else 'FAIL'} ({len(steps)} steps)")
    for name, ok in steps:
        print(f"  [{'OK' if ok else 'FAIL'}] {name}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
