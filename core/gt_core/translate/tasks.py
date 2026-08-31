"""翻译任务存储（路线图 3.3）：translate_tasks/translate_usage 表 DAO。

任务态（running/paused/cancelled/done/error）是**任务细粒度生命周期**，
不进项目状态机（项目 translating⇄reviewing 是粗粒度，见 ADR/路线图）。
同一连接串行访问（loop 线程），任务协程与 RPC 共享 project.conn。
"""

from __future__ import annotations

import sqlite3
import time
import uuid

from gt_core.rpc.models import TranslateStats, TranslateStatus, TranslateTask


class TaskStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @staticmethod
    def new_task_id() -> str:
        return uuid.uuid4().hex

    # ---------- 任务 CRUD ----------

    def create(self, *, provider_id: str, model: str, style_id: str | None,
               glossary_version: str, total: int) -> TranslateTask:
        task_id = self.new_task_id()
        now = time.time()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO translate_tasks(task_id, provider_id, model, style_id,
                                            glossary_version, status, total, done,
                                            created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'running', ?, 0, ?, ?)
                """,
                (task_id, provider_id, model, style_id, glossary_version, total, now, now),
            )
        return TranslateTask(
            task_id=task_id, status=TranslateStatus.running, provider_id=provider_id,
            model=model, style_id=style_id, glossary_version=glossary_version,
            total=total, done=0, error=None, created_at=now, updated_at=now,
        )

    def get(self, task_id: str) -> TranslateTask | None:
        row = self._conn.execute(
            "SELECT * FROM translate_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        return self._row_to_task(row) if row is not None else None

    def recent_task_id(self) -> str | None:
        row = self._conn.execute(
            "SELECT task_id FROM translate_tasks ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return row["task_id"] if row is not None else None

    def set_status(self, task_id: str, status: TranslateStatus | str, error: str | None = None) -> None:
        st = status.value if isinstance(status, TranslateStatus) else status
        with self._conn:
            self._conn.execute(
                "UPDATE translate_tasks SET status = ?, error = ?, updated_at = ? WHERE task_id = ?",
                (st, error, time.time(), task_id),
            )

    def set_done(self, task_id: str, done: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE translate_tasks SET done = ?, updated_at = ? WHERE task_id = ?",
                (done, time.time(), task_id),
            )

    def is_running(self, task_id: str) -> bool:
        t = self.get(task_id)
        return t is not None and t.status == TranslateStatus.running

    # ---------- 用量统计 ----------

    def record_usage(self, task_id: str, provider_id: str, model: str,
                     tokens_in: int, tokens_out: int) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO translate_usage(task_id, provider_id, model,
                                            tokens_in, tokens_out, estimated_cost, created_at)
                VALUES (?, ?, ?, ?, ?, 0, ?)
                """,
                (task_id, provider_id, model, tokens_in, tokens_out, time.time()),
            )

    def stats(self, task_id: str) -> TranslateStats | None:
        task = self.get(task_id)
        if task is None:
            return None
        row = self._conn.execute(
            """
            SELECT COALESCE(SUM(tokens_in), 0) AS ti, COALESCE(SUM(tokens_out), 0) AS to_
            FROM translate_usage WHERE task_id = ?
            """,
            (task_id,),
        ).fetchone()
        return TranslateStats(
            task_id=task_id, provider_id=task.provider_id, model=task.model,
            tokens_in=int(row["ti"]), tokens_out=int(row["to_"]),
            estimated_cost=0.0,  # MVP 不配单价；M4 成本面板再接单价配置
        )

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> TranslateTask:
        return TranslateTask(
            task_id=row["task_id"],
            status=TranslateStatus(row["status"]),
            provider_id=row["provider_id"],
            model=row["model"],
            style_id=row["style_id"],
            glossary_version=row["glossary_version"],
            total=int(row["total"]),
            done=int(row["done"]),
            error=row["error"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )
