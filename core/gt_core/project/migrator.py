"""迁移执行器（路线图 1.3）：按文件名序号顺序执行 SQL，meta 记录版本号。

项目文件向后兼容从这里开始：老版本项目被新版本打开时，逐级迁移到最新。
破坏性变更未来必须走"新迁移 + 数据搬迁"，不得修改已发布的迁移文件。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

# 迁移文件命名：NNN_描述.sql
_MIGRATION_RE = re.compile(r"^(\d{3})_.*\.sql$")

_SCHEMA_VERSION_KEY = "schema_version"


class MigrationError(RuntimeError):
    """迁移执行失败（版本不兼容或 SQL 错误）。"""


def current_version(conn: sqlite3.Connection) -> int:
    # 空库（meta 表不存在）视为版本 0：让 001_init.sql 负责建表
    has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if has_meta is None:
        return 0
    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?", (_SCHEMA_VERSION_KEY,)
    ).fetchone()
    if row is None:
        return 0
    return int(row[0])


def _list_migrations(migrations_dir: Path) -> list[tuple[int, Path]]:
    files: list[tuple[int, Path]] = []
    for p in sorted(migrations_dir.glob("*.sql")):
        m = _MIGRATION_RE.match(p.name)
        if m:
            files.append((int(m.group(1)), p))
    files.sort()
    return files


def migrate(conn: sqlite3.Connection, migrations_dir: Path | None = None) -> int:
    """把连接迁移到最新版本，返回迁移后的版本号。

    migrations_dir 默认取本模块同级 migrations/。每个迁移一个事务：脚本包
    BEGIN IMMEDIATE...COMMIT，全部 DDL + 版本号一起提交或回滚。
    不能按分号拆分逐条 execute（trigger 的 BEGIN...END 内部也有分号），
    也不能裸 executescript（它会隐式 COMMIT 挂起事务导致非原子，review 实测）。
    库版本高于本二进制支持的最大迁移号时抛 MigrationError（未来版本项目）。
    """
    if migrations_dir is None:
        migrations_dir = Path(__file__).resolve().parent / "migrations"
    version = current_version(conn)
    migrations = _list_migrations(migrations_dir)
    for next_version, file in migrations:
        if next_version <= version:
            continue  # 已应用，跳过（允许旧的迁移文件仍在目录里）
        if next_version != version + 1:
            raise MigrationError(
                f"迁移版本跳跃: 当前 {version}，遇到 {next_version}（迁移必须连续）"
            )
        sql = file.read_text(encoding="utf-8")
        version_sql = (
            f"UPDATE meta SET value = {next_version} WHERE key = '{_SCHEMA_VERSION_KEY}';"
        )
        script = "BEGIN IMMEDIATE;\n" + sql + "\n" + version_sql + "\nCOMMIT;"
        try:
            conn.executescript(script)
        except sqlite3.Error:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        version = next_version
    max_known = migrations[-1][0] if migrations else 0
    if version > max_known:
        raise MigrationError(
            f"项目由更新版本创建: schema_version={version}，本二进制最高支持 {max_known}"
        )
    return version
