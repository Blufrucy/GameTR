"""SQLite 连接管理（路线图 1.3）。

- WAL 模式：读写并发不互相阻塞（GUI 读 + 流水线写共存）
- 外键开启 + Row 工厂（按列名取值）
- 连接按路径缓存，close 时释放
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# 项目内可用的特殊内存路径
_MEMORY = ":memory:"


def connect(db_path: str | Path, *, wal: bool = True) -> sqlite3.Connection:
    """打开（或创建）项目数据库连接。

    wal=False 用于内存库或测试（内存库设 WAL 无意义，PRAGMA 返回 memory）。
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if wal and str(db_path) != _MEMORY:
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


class ConnectionManager:
    """按路径持有单条连接；重复 open 复用，close 后释放。

    设计取舍：sidecar 进程一次服务一个项目（M1），单连接足够；
    不引入连接池，SQLite WAL 下单连接 + 事务纪律最可控。
    """

    def __init__(self) -> None:
        self._conn: sqlite3.Connection | None = None
        self._path: Path | None = None

    def get(self, db_path: str | Path) -> sqlite3.Connection:
        path = Path(db_path).resolve()
        if self._conn is not None and self._path == path:
            return self._conn
        self.close()
        self._conn = connect(path)
        self._path = path
        return self._conn

    def adopt(self, conn: sqlite3.Connection, db_path: str | Path) -> None:
        """接管一条已建好的连接（create() 建库后不重复 open）。"""
        self.close()
        self._conn = conn
        self._path = Path(db_path).resolve()

    @property
    def is_open(self) -> bool:
        return self._conn is not None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            self._path = None
