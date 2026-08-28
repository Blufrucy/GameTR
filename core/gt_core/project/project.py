"""项目门面（路线图 1.3/1.5）：create / open / close / stats。

一次服务一个项目（sidecar 进程语义，GUI 一次开一个工程）。
项目状态（project_state）存 meta 表，变更走状态机守卫（pipeline.transition_project）。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from gt_core.pipeline import transition_project
from gt_core.project.db import ConnectionManager, connect
from gt_core.project.migrator import current_version, migrate
from gt_core.project.repo import Repo
from gt_core.rpc.models import ProjectInfo, ProjectState, ProjectStats

_STATE_KEY = "project_state"
_ENGINE_KEY = "engine_id"
_SOURCE_KEY = "source_path"
_CREATED_KEY = "created_at"


class Project:
    """打开中的项目。持有连接与 DAO；close() 后不可再用。"""

    def __init__(self, conn_manager: ConnectionManager, path: Path) -> None:
        self._cm = conn_manager
        self.path = path
        self.conn = conn_manager.get(path)
        self.repo = Repo(self.conn)

    # ---------- 生命周期 ----------

    @classmethod
    def create(cls, path: str | Path, *, engine_id: str, source_path: str) -> Project:
        """新建项目文件：连库 + 跑迁移 + 写 meta。已存在时报错（避免覆盖）。"""
        p = Path(path).resolve()
        if p.exists():
            raise FileExistsError(f"项目文件已存在: {p}")
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = connect(p)
        try:
            migrate(conn)  # 建表 + schema_version=1
            meta = {
                _ENGINE_KEY: engine_id,
                _SOURCE_KEY: source_path,
                _STATE_KEY: ProjectState.created.value,
                _CREATED_KEY: str(time.time()),
            }
            conn.executemany(
                "INSERT INTO meta(key, value) VALUES (?, ?)", meta.items()
            )
            conn.commit()  # meta 必须落盘：否则 close 后 reopen 读不到（未提交回滚）
        except Exception:
            conn.close()
            p.unlink(missing_ok=True)  # 建失败不留半成品文件
            raise
        cm = ConnectionManager()
        cm.adopt(conn, p)  # 直接接管已建连接，避免重复 open
        return cls(cm, p)

    @classmethod
    def open(cls, path: str | Path) -> Project:
        """打开已有项目：跑迁移（老版本自动升级）+ 校验 schema_version。"""
        p = Path(path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"项目文件不存在: {p}")
        cm = ConnectionManager()
        conn = cm.get(p)
        migrate(conn)  # 向后兼容：老项目升级到最新
        return cls(cm, p)

    def close(self) -> None:
        self._cm.close()

    def info(self) -> ProjectInfo:
        meta = dict(self.conn.execute("SELECT key, value FROM meta").fetchall())
        return ProjectInfo(
            path=str(self.path),
            engine_id=meta.get(_ENGINE_KEY, ""),
            source_path=meta.get(_SOURCE_KEY, ""),
            project_state=ProjectState(meta.get(_STATE_KEY, ProjectState.created.value)),
            schema_version=current_version(self.conn),
            created_at=float(meta.get(_CREATED_KEY, 0.0)),
        )

    # ---------- 状态 ----------

    def get_state(self) -> ProjectState:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = ?", (_STATE_KEY,)
        ).fetchone()
        return ProjectState(row["value"]) if row is not None else ProjectState.created

    def set_state(self, target: ProjectState) -> ProjectState:
        """带守卫的项目状态迁移；非法迁移抛 InvalidStateTransition。"""
        state = transition_project(self.get_state(), target)
        with self.conn:
            self.conn.execute(
                "UPDATE meta SET value = ? WHERE key = ?",
                (state.value, _STATE_KEY),
            )
        return state

    # ---------- 统计 ----------

    def stats(self) -> ProjectStats:
        return self.repo.stats()

    def meta_map(self) -> dict[str, Any]:
        """meta 表全量读取（诊断/回放用）。"""
        return dict(self.conn.execute("SELECT key, value FROM meta").fetchall())
