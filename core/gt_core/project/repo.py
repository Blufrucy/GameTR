"""DAO 层（路线图 1.3）：entries / glossary 的所有存取。

事务纪律：批量插入与批量改状态各一个事务；单条更新一个事务（路线图要求）。
FTS 由 migrations/001_init.sql 的 triggers 自动同步，DAO 不碰 FTS 表。
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable
from typing import Any

from gt_core.pipeline import (
    can_batch_set_status,
    transition_entry,
)
from gt_core.rpc.models import Entry, EntryStatus, GlossaryEntry, Page, ProjectStats

# 哨兵：区分「字段未传」（默认）与「显式传 null」（清空译文）。None 本身是合法值。
_UNSET = object()

# IN 子句每块条数：远低于 SQLITE_LIMIT_VARIABLE_NUMBER（默认 32766），留足余量
_CHUNK = 500


def _entry_to_row(e: Entry, now: float | None = None) -> tuple[Any, ...]:
    """Entry -> 行值。locators_json 存单元素数组（首个元素即 Entry.locator）。"""
    ts = now if now is not None else time.time()
    return (
        e.id,
        e.source,
        e.translation,
        int(e.status),
        json.dumps([e.locator], ensure_ascii=False),
        e.context_json,
        e.warnings_json,
        ts,
    )


def _row_to_entry(row: sqlite3.Row) -> Entry:
    """行值 -> Entry。locator 取 locators_json 数组首个元素。"""
    return Entry(
        id=row["id"],
        source=row["source"],
        translation=row["translation"],
        status=EntryStatus(row["status"]),
        locator=json.loads(row["locators_json"])[0],
        context_json=row["context_json"],
        warnings_json=row["warnings_json"],
        updated_at=row["updated_at"],
    )


class Repo:
    """单连接 DAO。所有写操作带事务（调用方也可包外层事务）。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ---------- entries ----------

    def upsert_entries(self, entries: Iterable[Entry], *, commit: bool = True) -> int:
        """批量插入或更新（按 id upsert，保留 rowid；2 万条 <2s）。

        同 id 已存在则覆盖 source/translation/status/locators/context/warnings
        并刷新 updated_at；不存在的插入。返回影响行数。
        """
        rows = [_entry_to_row(e) for e in entries]
        with self._conn:  # 整批一个事务
            self._conn.executemany(
                """
                INSERT INTO entries(id, source, translation, status, locators_json,
                                    context_json, warnings_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  source = excluded.source,
                  translation = excluded.translation,
                  status = excluded.status,
                  locators_json = excluded.locators_json,
                  context_json = excluded.context_json,
                  warnings_json = excluded.warnings_json,
                  updated_at = excluded.updated_at
                """,
                rows,
            )
        return len(rows)

    def upsert_extracted(self, entries: Iterable[Entry], *, commit: bool = True) -> int:
        """extract 落库（M2）：已存在的条目只刷新源侧字段。

        只更新 source/locators/context/warnings，**不覆盖 translation/status**：
        稳定 ID = sha1(engine+locator+source)，同 id 说明原文没变，译文仍有效，
        重提取（游戏更新后）不得抹掉已翻译/已确认内容。
        """
        rows = [_entry_to_row(e) for e in entries]
        with self._conn:  # 整批一个事务
            self._conn.executemany(
                """
                INSERT INTO entries(id, source, translation, status, locators_json,
                                    context_json, warnings_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  source = excluded.source,
                  locators_json = excluded.locators_json,
                  context_json = excluded.context_json,
                  warnings_json = excluded.warnings_json
                """,
                rows,
            )
        return len(rows)

    def upsert_translations(self, entries: Iterable[Entry], *, commit: bool = True,
                            overwrite_confirmed: bool = False) -> int:
        """翻译落库（M3）：覆盖 translation、置 MACHINE，**默认跳过 CONFIRMED**。

        与 upsert_extracted 语义相反（镜像）：翻译结果要写 translation/status，
        但「重翻不覆盖人工确认」纪律必须守（M1 batch_update_status 同样跳 CONFIRMED）。

        并发安全：ON CONFLICT 的 UPDATE 里 WHERE status != 4（CONFIRMED）在**同一条
        SQL 原子**完成——防「跨协程先查后改」竞态把用户刚确认的内容打回 MACHINE。
        FTS 由 entries_au trigger 在 translation 变化时自动同步，无需手工维护。
        返回实际更新的行数（CONFIRMED 跳过的不计入）。

        overwrite_confirmed=True 时去掉守卫（translate.start overwrite 语义，
        显式重译已确认条目；默认 false 保持纪律）。
        """
        guard = "" if overwrite_confirmed else "WHERE status != 4"
        rows = [_entry_to_row(e) for e in entries]
        with self._conn:  # 每批一个事务（流水线批边界）
            cur = self._conn.executemany(
                f"""
                INSERT INTO entries(id, source, translation, status, locators_json,
                                    context_json, warnings_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  translation = excluded.translation,
                  status = excluded.status,
                  warnings_json = excluded.warnings_json,
                  updated_at = excluded.updated_at
                {guard}
                """,
                rows,
            )
        return cur.rowcount

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS c FROM entries").fetchone()
        return int(row["c"])

    def get(self, entry_id: str) -> Entry | None:
        row = self._conn.execute(
            "SELECT * FROM entries WHERE id = ?", (entry_id,)
        ).fetchone()
        return _row_to_entry(row) if row is not None else None

    def list_entries(
        self,
        *,
        page: int = 1,
        page_size: int = 200,
        status: EntryStatus | None = None,
        file_path: str | None = None,
    ) -> Page:
        """分页查询，可按状态过滤、按文件过滤（context_json.file_path）。"""
        where: list[str] = []
        args: list[Any] = []
        if status is not None:
            where.append("status = ?")
            args.append(int(status))
        if file_path is not None:
            # context_json 由插件在提取时写入 {"file_path": ...}（见 M2 extract）
            # json_valid 防护：任一行 malformed JSON 时 json_extract 会整体抛错，
            # 把坏行按「无匹配」处理而不是拖垮整页查询（review 实测复现）
            where.append(
                "(CASE WHEN json_valid(context_json) THEN "
                "json_extract(context_json, '$.file_path') ELSE NULL END) = ?"
            )
            args.append(file_path)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        total = int(
            self._conn.execute(
                f"SELECT COUNT(*) AS c FROM entries {where_sql}", args
            ).fetchone()["c"]
        )
        args += [page_size, (page - 1) * page_size]
        rows = self._conn.execute(
            f"SELECT * FROM entries {where_sql} ORDER BY rowid LIMIT ? OFFSET ?",
            args,
        ).fetchall()
        return Page(
            items=[_row_to_entry(r) for r in rows],
            page=page,
            page_size=page_size,
            total=total,
        )

    def update(self, entry_id: str, *, translation: object = _UNSET,
               status: object = _UNSET) -> Entry | None:
        """单条更新译文/状态（单条一个事务）。

        - translation/status 传 _UNSET（默认）表示不改该字段
        - translation 传 None 表示显式清空译文（协议允许 null）
        - status 迁移经状态机校验（非法迁移抛 InvalidStateTransition）
        - 空操作（两字段都没传或值无变化）不刷新 updated_at（review 修复）
        """
        current = self.get(entry_id)
        if current is None:
            return None
        if translation is _UNSET and status is _UNSET:
            return current  # 纯 id 空操作：不动时间戳
        new_translation = current.translation if translation is _UNSET else translation
        new_status = current.status if status is _UNSET else status
        if not isinstance(new_translation, str | None) or not isinstance(new_status, EntryStatus):
            raise TypeError(f"非法更新参数: translation={new_translation!r}, status={new_status!r}")
        if status is not _UNSET and new_status != current.status:
            transition_entry(current.status, new_status)  # 非法迁移抛 InvalidStateTransition
        if new_translation == current.translation and new_status == current.status:
            return current  # 值无实际变化：不刷 updated_at
        with self._conn:  # 单条一个事务（路线图纪律）
            self._conn.execute(
                """
                UPDATE entries SET translation = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_translation, int(new_status), time.time(), entry_id),
            )
        return self.get(entry_id)

    def batch_update_status(self, ids: Iterable[str], status: EntryStatus) -> int:
        """批量改状态（一键确认/重翻等）。CONFIRMED 条目跳过（重翻不得覆盖）。

        返回实际更新的条数。整批一个事务；ids 按 _CHUNK 分块，避免
        SQLITE_LIMIT_VARIABLE_NUMBER（默认 32766）超限（review：10 万条全选会崩）。
        """
        id_list = list(ids)
        if not id_list:
            return 0
        total_updated = 0
        for start in range(0, len(id_list), _CHUNK):
            chunk = id_list[start:start + _CHUNK]
            # 查出目标状态，跳过 CONFIRMED 与已处于目标状态的条目
            marks = ",".join("?" for _ in chunk)
            rows = self._conn.execute(
                f"SELECT id, status FROM entries WHERE id IN ({marks})", chunk
            ).fetchall()
            target_ids = [
                r["id"] for r in rows
                if can_batch_set_status(EntryStatus(r["status"]), status)
            ]
            if not target_ids:
                continue
            marks2 = ",".join("?" for _ in target_ids)
            with self._conn:  # 每块一个事务（整批语义由调用方外层保证）
                cur = self._conn.execute(
                    f"""
                    UPDATE entries SET status = ?, updated_at = ? WHERE id IN ({marks2})
                    """,
                    [int(status), time.time(), *target_ids],
                )
            total_updated += cur.rowcount
        return total_updated

    def search(self, query: str, *, page: int = 1, page_size: int = 200) -> Page:
        """全文搜索 source/translation。

        策略：>=3 字符走 FTS5 trigram（子串匹配，相关度排序）；
        更短查询 trigram 无法匹配（至少 3 字符），降级 LIKE 全表扫
        （5 万条规模 <50ms，见 test_perf）。
        """
        q = query.strip()
        if len(q) >= 3:
            return self._search_fts(q, page, page_size)
        return self._search_like(q, page, page_size)

    def _search_fts(self, q: str, page: int, page_size: int) -> Page:
        match = '"' + q.replace('"', '""') + '"'  # phrase：避免 FTS 语法注入
        total = int(
            self._conn.execute(
                "SELECT COUNT(*) AS c FROM entries_fts WHERE entries_fts MATCH ?",
                (match,),
            ).fetchone()["c"]
        )
        rows = self._conn.execute(
            """
            SELECT e.* FROM entries e
            JOIN entries_fts f ON f.rowid = e.rowid
            WHERE entries_fts MATCH ?
            ORDER BY bm25(entries_fts)
            LIMIT ? OFFSET ?
            """,
            (match, page_size, (page - 1) * page_size),
        ).fetchall()
        return Page(
            items=[_row_to_entry(r) for r in rows],
            page=page,
            page_size=page_size,
            total=total,
        )

    def _search_like(self, q: str, page: int, page_size: int) -> Page:
        """短查询降级：LIKE 子串匹配（% 与 _ 转义，防通配符注入）。"""
        pat = "%" + q.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"
        where = "(source LIKE ? ESCAPE '\\' OR translation LIKE ? ESCAPE '\\')"
        total = int(
            self._conn.execute(f"SELECT COUNT(*) AS c FROM entries WHERE {where}",
                               (pat, pat)).fetchone()["c"]
        )
        rows = self._conn.execute(
            f"SELECT * FROM entries WHERE {where} ORDER BY rowid LIMIT ? OFFSET ?",
            (pat, pat, page_size, (page - 1) * page_size),
        ).fetchall()
        return Page(
            items=[_row_to_entry(r) for r in rows],
            page=page,
            page_size=page_size,
            total=total,
        )

    # ---------- glossary ----------

    def glossary_list(self) -> list[GlossaryEntry]:
        rows = self._conn.execute(
            "SELECT id, term, translation, match_case FROM glossary ORDER BY term"
        ).fetchall()
        return [
            GlossaryEntry(
                id=r["id"],
                term=r["term"],
                translation=r["translation"],
                match_case=bool(r["match_case"]),
            )
            for r in rows
        ]

    def glossary_upsert(self, term: str, translation: str,
                        match_case: bool = False) -> GlossaryEntry:
        """按 term 更新或插入；返回条目（含数据库生成的 id）。单条一个事务。

        ON CONFLICT DO UPDATE 路径的 rowcount/lastrowid 不可靠（review 实测：
        冲突更新后 lastrowid 仍是上次真正 INSERT 的过期值），故始终重查真实 id。
        """
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO glossary(term, translation, match_case)
                VALUES (?, ?, ?)
                ON CONFLICT(term) DO UPDATE SET
                  translation = excluded.translation,
                  match_case = excluded.match_case
                """,
                (term, translation, int(match_case)),
            )
            row = self._conn.execute(
                "SELECT id FROM glossary WHERE term = ?", (term,)
            ).fetchone()
            gid = int(row["id"]) if row is not None else 0
        return GlossaryEntry(
            id=gid, term=term, translation=translation, match_case=match_case
        )

    def glossary_delete(self, term: str) -> int:
        """删除术语（M4 术语表 CRUD 补齐）。返回删除行数（不存在为 0）。"""
        with self._conn:
            cur = self._conn.execute("DELETE FROM glossary WHERE term = ?", (term,))
        return cur.rowcount

    # ---------- stats ----------

    def stats(self) -> ProjectStats:
        total = self.count()
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS c FROM entries GROUP BY status"
        ).fetchall()
        by_status = {str(r["status"]): int(r["c"]) for r in rows}
        return ProjectStats(total=total, by_status=by_status)
