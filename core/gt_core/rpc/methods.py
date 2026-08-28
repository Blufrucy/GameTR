"""核心 RPC 方法注册。

- core.*：健康检查 / 退出 / 日志级别（M0）
- project.*、entries.*、glossary.*：M1 项目内核（方法清单见 rpc-methods.json）
- detect/extract/plugins/translate/providers：M2/M3 占位（未注册，报 METHOD_NOT_FOUND）

约定：返回的 pydantic 模型一律 model_dump(mode="json") 转 dict 再序列化
（IntEnum/StrEnum 需要 json 模式才可 JSON 序列化）。
"""

from __future__ import annotations

import os
import time
from typing import Any

from pydantic import BaseModel

import gt_core
from gt_core.pipeline import InvalidStateTransition
from gt_core.project import Project
from gt_core.project.repo import _UNSET
from gt_core.rpc.errors import RpcError, RpcErrorCode
from gt_core.rpc.params import (
    EmptyParams,
    EntriesBatchStatusParams,
    EntriesGetParams,
    EntriesListParams,
    EntriesSearchParams,
    EntriesUpdateParams,
    GlossaryUpsertParams,
    ProjectCreateParams,
    ProjectOpenParams,
)
from gt_core.rpc.server import MethodRegistry

_LOG_LEVELS = ("debug", "info", "warn", "error")


def _dump(model: BaseModel) -> dict[str, Any]:
    """pydantic 模型 -> JSON 可序列化 dict（枚举转基础类型）。"""
    return model.model_dump(mode="json")


def _require_project(ctx: dict[str, Any]) -> Project:
    project = ctx.get("project")
    if not isinstance(project, Project):
        raise RpcError(RpcErrorCode.NO_PROJECT, "未打开项目（先调 project.create/open）")
    return project


def _project_error(exc: Exception) -> RpcError:
    """把项目层异常映射为 PROJECT_ERROR（文件/迁移/状态机守卫）。"""
    return RpcError(RpcErrorCode.PROJECT_ERROR, f"项目操作失败: {exc}")


def register_core_methods() -> MethodRegistry:
    """core.* + M1 全量方法（project/entries/glossary）。"""
    reg = MethodRegistry()

    # ---------- core.* ----------

    @reg.register("core.ping", EmptyParams)
    def ping(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """健康检查：GUI 启动时以 500ms 间隔重试直到成功（路线图 1.3）。"""
        return {
            "pong": True,
            "version": gt_core.__version__,
            "pid": os.getpid(),
            "ts": time.time(),
        }

    @reg.register("core.shutdown", EmptyParams)
    def shutdown(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """优雅退出：置停止标志，主循环下一轮结束。"""
        reg.request_shutdown()
        return {"ok": True}

    @reg.register("core.log_level")
    def log_level(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """读写进程日志级别（M0 仅占位，供后续日志过滤使用）。"""
        level = params.get("level")
        if level is not None:
            if level not in _LOG_LEVELS:
                raise RpcError(RpcErrorCode.INVALID_PARAMS, f"level 必须是 {_LOG_LEVELS}")
            ctx["log_level"] = level
        return {"level": ctx.get("log_level", "info")}

    # ---------- project.* ----------

    @reg.register("project.create", ProjectCreateParams)
    def project_create(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """创建项目（SQLite + 迁移 + meta），并设为当前项目。"""
        try:
            project = Project.create(
                params["path"], engine_id=params["engine_id"], source_path=params["source_path"]
            )
        except FileExistsError as exc:
            raise RpcError(RpcErrorCode.PROJECT_ERROR, str(exc)) from exc
        _close_if_any(ctx)
        ctx["project"] = project
        return _dump(project.info())

    @reg.register("project.open", ProjectOpenParams)
    def project_open(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """打开已有项目（老版本自动迁移），设为当前项目。"""
        try:
            project = Project.open(params["path"])
        except FileNotFoundError as exc:
            raise RpcError(RpcErrorCode.PROJECT_ERROR, str(exc)) from exc
        _close_if_any(ctx)
        ctx["project"] = project
        return _dump(project.info())

    @reg.register("project.close", EmptyParams)
    def project_close(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """关闭当前项目（无项目时幂等返回 ok）。"""
        _close_if_any(ctx)
        return {"ok": True}

    @reg.register("project.stats", EmptyParams)
    def project_stats(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """项目统计：条目总数 + 状态分布。"""
        return _dump(_require_project(ctx).stats())

    # ---------- entries.* ----------

    @reg.register("entries.list", EntriesListParams)
    def entries_list(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """分页查询条目，可按状态过滤、按文件过滤。"""
        repo = _require_project(ctx).repo
        return _dump(repo.list_entries(**params))

    @reg.register("entries.get", EntriesGetParams)
    def entries_get(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """按 id 取单条；不存在报 PROJECT_ERROR。"""
        entry = _require_project(ctx).repo.get(params["id"])
        if entry is None:
            raise RpcError(RpcErrorCode.PROJECT_ERROR, f"条目不存在: {params['id']}")
        return _dump(entry)

    @reg.register("entries.update", EntriesUpdateParams)
    def entries_update(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """更新单条译文/状态（状态迁移有守卫）。

        params 是 exclude_unset 的 dict：未传字段无键（传给 _UNSET = 不改），
        显式传 translation=null 传 None = 清空译文（review 修复）。
        """
        repo = _require_project(ctx).repo
        try:
            entry = repo.update(
                params["id"],
                translation=params.get("translation", _UNSET),
                status=params.get("status", _UNSET),
            )
        except InvalidStateTransition as exc:
            raise RpcError(RpcErrorCode.PROJECT_ERROR, str(exc)) from exc
        if entry is None:
            raise RpcError(RpcErrorCode.PROJECT_ERROR, f"条目不存在: {params['id']}")
        return _dump(entry)

    @reg.register("entries.batch_update_status", EntriesBatchStatusParams)
    def entries_batch_status(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """批量改状态；CONFIRMED 条目跳过（一键重翻不覆盖人工确认）。"""
        repo = _require_project(ctx).repo
        updated = repo.batch_update_status(params["ids"], params["status"])
        return {"updated": updated}

    @reg.register("entries.search", EntriesSearchParams)
    def entries_search(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """FTS 全文搜索 source/translation。"""
        repo = _require_project(ctx).repo
        return _dump(repo.search(params["query"], page=params.get("page", 1),
                                 page_size=params.get("page_size", 200)))

    # ---------- glossary.* ----------

    @reg.register("glossary.list", EmptyParams)
    def glossary_list(params: dict[str, Any], ctx: dict[str, Any]) -> list[dict[str, Any]]:
        """术语表全量列表。"""
        return [_dump(g) for g in _require_project(ctx).repo.glossary_list()]

    @reg.register("glossary.upsert", GlossaryUpsertParams)
    def glossary_upsert(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """新增/更新术语（按 term upsert）。"""
        repo = _require_project(ctx).repo
        entry = repo.glossary_upsert(params["term"], params["translation"],
                                     match_case=params.get("match_case", False))
        return _dump(entry)

    return reg


def _close_if_any(ctx: dict[str, Any]) -> None:
    """换项目前关闭旧连接，避免句柄泄漏。"""
    project = ctx.get("project")
    if isinstance(project, Project):
        project.close()
    ctx.pop("project", None)
