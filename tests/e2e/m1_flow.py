#!/usr/bin/env python
"""M1 验收脚本（路线图 1.6 验收标准）：CLI + curl 风格全流程。

流程：创建项目 → 插入 10 万条 → 分页查询 → 改状态 → FTS 搜索 → 保存重开 →
      RPC 日志可回放。

运行：
  python tests/e2e/m1_flow.py                # 10 万条（验收）
  python tests/e2e/m1_flow.py --rows 500     # 快速冒烟

说明：10 万条插入不经过 RPC（M1 协议无批量导入方法，条目由 M2 的 extract.run
产生），脚本直接走 DAO 批量插入，随后全部查询/改状态/搜索走真实 stdio 管道。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from gt_core.ir import entry_id
from gt_core.project import Project
from gt_core.rpc.methods import register_core_methods
from gt_core.rpc.models import Entry, EntryStatus
from gt_core.rpc.server import _process_line

_GAME_PATH = "/fake/game"


class RpcClient:
    """子进程 stdio 管道客户端：一行一个 JSON-RPC 请求/响应。"""

    def __init__(self, proc: subprocess.Popen) -> None:
        self._proc = proc
        self._seq = 0

    def call(self, method: str, **params) -> dict:
        self._seq += 1
        req = {"jsonrpc": "2.0", "id": self._seq, "method": method, "params": params}
        self._proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()
        resp = self._proc.stdout.readline()
        if not resp:
            raise RuntimeError("serve 进程退出，无响应")
        data = json.loads(resp)
        if data.get("id") != self._seq:
            raise RuntimeError(f"响应 id 不匹配: {data}")
        if "error" in data:
            raise AssertionError(f"{method} 失败: {data['error']}")
        return data["result"]


def run_flow(rows: int) -> None:
    checks: list[str] = []
    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        ok = ok and cond
        checks.append(f"[{'OK' if cond else 'FAIL'}] {name}")

    with tempfile.TemporaryDirectory(prefix="m1-e2e-", ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)
        proj_path = tmp / "proj.gametr.sqlite3"

        # ---- 阶段 1：创建项目 + 批量插入（DAO，extract 在 M2 走 RPC） ----
        project = Project.create(proj_path, engine_id="rpgmv", source_path=_GAME_PATH)
        t0 = time.perf_counter()
        batch: list[Entry] = []
        for i in range(rows):
            source = f"こんにちは勇者{i} 世界の旅へ"
            batch.append(Entry(
                id=entry_id("rpgmv", f"loc:{i}", source),
                source=source, translation=None,
                status=EntryStatus.PENDING, locator=f"loc:{i}",
                context_json='{"file_path": "Map001.json"}', updated_at=0.0,
            ))
            if len(batch) >= 20_000:
                project.repo.upsert_entries(batch)
                batch = []
        if batch:
            project.repo.upsert_entries(batch)
        insert_s = time.perf_counter() - t0
        check(f"创建项目 + 插入 {rows} 条（{insert_s:.2f}s）", project.repo.count() == rows)
        project.close()

        # ---- 阶段 2：真实 stdio 管道重开 + 全部操作 ----
        proc = subprocess.Popen(
            [sys.executable, "-m", "gt_core", "serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", cwd=str(tmp),
        )
        client = RpcClient(proc)
        try:
            info = client.call("project.open", path=str(proj_path))
            check("project.open（保存重开）", info["engine_id"] == "rpgmv")

            page = client.call("entries.list", page=1, page_size=200)
            check(f"entries.list 分页（total={page['total']}）", page["total"] == rows)

            # 挑一页改状态
            ids = [e["id"] for e in page["items"][:100]]
            updated = client.call("entries.batch_update_status", ids=ids, status=2)
            check(f"entries.batch_update_status（{updated['updated']} 条 -> MACHINE）",
                  updated["updated"] == 100)

            # 查询词必须存在于数据中（trigram 是 AND 语义：同一行含全部 3-gram）
            hits = client.call("entries.search", query="世界の旅", page_size=50)
            check(f"entries.search FTS（{hits['total']} 命中）", hits["total"] == rows)

            short = client.call("entries.search", query="勇者", page_size=10)
            check(f"entries.search LIKE 短查询（{short['total']} 命中）", short["total"] == rows)

            got = client.call("entries.get", id=ids[0])
            check("entries.get 单条", got["id"] == ids[0] and got["status"] == 2)

            updated_e = client.call("entries.update", id=ids[0], translation="翻訳テキスト", status=3)
            check("entries.update 译文+状态", updated_e["translation"] == "翻訳テキスト")

            stats = client.call("project.stats")
            check(f"project.stats（total={stats['total']}）", stats["total"] == rows)

            client.call("glossary.upsert", term="勇者", translation="hero")
            gl = client.call("glossary.list")
            check("glossary upsert/list", len(gl) == 1 and gl[0]["term"] == "勇者")

            client.call("core.shutdown")
            proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
        err = proc.stderr.read()
        check("serve 进程干净退出（无异常）", "Traceback" not in err)

        # ---- 阶段 3：RPC 日志可回放 ----
        log_files = sorted((tmp / "logs").glob("rpc-*.ndjson"))
        replay_reg = register_core_methods()
        replay_ctx: dict = {}
        replayed = 0
        for lf in log_files:
            for line in lf.read_text(encoding="utf-8").splitlines():
                rec = json.loads(line)
                if rec.get("t") == "req":
                    _process_line(rec["line"], replay_reg, replay_ctx)  # 回放不崩溃即通过
                    replayed += 1
        check(f"RPC 日志可回放（{len(log_files)} 文件, {replayed} 请求重放）", replayed > 0)

    print(f"\nM1 验收：{'PASS' if ok else 'FAIL'}（{len(checks)} 项）")
    for c in checks:
        print("  " + c)
    if not ok:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="M1 验收脚本")
    parser.add_argument("--rows", type=int, default=100_000, help="插入条数（默认 10 万）")
    args = parser.parse_args()
    run_flow(args.rows)


if __name__ == "__main__":
    main()
