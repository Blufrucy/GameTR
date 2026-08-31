"""核心 RPC 方法注册。

- core.*：健康检查 / 退出 / 日志级别（M0）
- project.*、entries.*、glossary.*：M1 项目内核（方法清单见 rpc-methods.json）
- detect/extract/write_back/plugins：M2 插件框架（已注册，缺插件时报 ENGINE_NOT_SUPPORTED）
- translate.*/providers.*/glossary.delete：M3 翻译流水线（已注册，见 ADR-0007）

约定：返回的 pydantic 模型一律 model_dump(mode="json") 转 dict 再序列化
（IntEnum/StrEnum 需要 json 模式才可 JSON 序列化）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

import gt_core
from gt_core.ir import entry_id
from gt_core.pipeline import InvalidStateTransition
from gt_core.plugin import PluginManager
from gt_core.project import Project
from gt_core.project.repo import _UNSET, Repo
from gt_core.providers import ProviderManager
from gt_core.rpc.errors import RpcError, RpcErrorCode
from gt_core.rpc.models import (
    DetectResult,
    Entry,
    EntryStatus,
    ExtractResult,
    GlossaryEntry,
    ProjectState,
    TranslateTask,
    WriteBackResult,
)
from gt_core.rpc.params import (
    DetectRunParams,
    EmptyParams,
    EntriesBatchStatusParams,
    EntriesGetParams,
    EntriesListParams,
    EntriesSearchParams,
    EntriesUpdateParams,
    ExtractRunParams,
    GlossaryDeleteParams,
    GlossaryUpsertParams,
    ProjectCreateParams,
    ProjectDefaultPathParams,
    ProjectOpenParams,
    ProviderConfigureParams,
    ProviderModelsParams,
    ProviderTestParams,
    TranslateExportParams,
    TranslateImportParams,
    TranslateRetranslateParams,
    TranslateStartParams,
    TranslateStatsParams,
    TranslateStatusParams,
    TranslateTaskParams,
    WriteBackRunParams,
)
from gt_core.rpc.server import MethodRegistry
from gt_core.translate import format_glossary
from gt_core.translate.runner import run_translate_task
from gt_core.translate.tasks import TaskStore

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

    @reg.register("project.default_path", ProjectDefaultPathParams)
    def project_default_path(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """计算项目文件默认路径（~/.gametr/projects/<游戏目录slug>.sqlite3）。

        前端导入时先调此方法拿 path，再 project.create/open——
        避免依赖 Tauri path API（homeDir/join 的权限细节），路径统一由核心算。
        """
        slug = re.sub(r"[^a-zA-Z0-9一-鿿]", "_", params["dir"])[-40:] or "game"
        return {"path": str(Path.home() / ".gametr" / "projects" / f"{slug}.sqlite3")}

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

    # ---------- M2：detect / extract / write_back / plugins（插件框架） ----------

    @reg.register("detect.run", DetectRunParams)
    def detect_run(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """探测游戏目录引擎：遍历已加载插件取置信度最高者。独立于项目。

        识别失败（无插件或全部置信度 0）报 ENGINE_NOT_SUPPORTED。
        """
        best: DetectResult | None = None
        for p in _plugin_manager().loaded_plugins():
            assert p.entry is not None  # loaded 契约保证
            try:
                r = DetectResult.model_validate(p.entry.detect(params["dir"]))
            except Exception:  # noqa: BLE001 — 单个插件探测失败不影响其他
                continue
            if best is None or r.confidence > best.confidence:
                best = r
        if best is None or best.confidence <= 0:
            raise RpcError(RpcErrorCode.ENGINE_NOT_SUPPORTED, f"无法识别目录: {params['dir']}")
        return _dump(best)

    @reg.register("extract.run", ExtractRunParams)
    def extract_run(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """提取条目并落库（source_path 取当前项目）。同步返回，progress 通知留 M3。

        插件只返回 locator/source/context/warnings，核心算稳定 ID 并落库
        （upsert_extracted：重提取不覆盖已翻译内容）。
        """
        project = _require_project(ctx)
        info = project.info()
        entry_mod = _plugin_manager().get_entry(info.engine_id)
        t0 = time.time()
        try:
            raw_entries = list(entry_mod.extract(info.source_path))
            entries = [_build_entry(info.engine_id, raw, info.source_path) for raw in raw_entries]
        except Exception as exc:  # noqa: BLE001 — 插件异常映射为项目错误
            raise RpcError(RpcErrorCode.PROJECT_ERROR, f"提取失败: {exc}") from exc
        # 首次（created）走 detecting→extracted；重导（已 extracted/done/...）幂等重置回 extracted
        if project.get_state() == ProjectState.created:
            project.set_state(ProjectState.detecting)
            project.set_state(ProjectState.extracted)
        else:
            project.set_state(ProjectState.extracted)
        project.repo.upsert_extracted(entries)
        return _dump(ExtractResult(
            engine_id=info.engine_id,
            extracted_count=len(entries),
            duration_ms=(time.time() - t0) * 1000.0,
        ))

    @reg.register("write_back.run", WriteBackRunParams)
    def write_back_run(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """回写译文：插件拷贝游戏 + 字节区间替换，输出到指定目录（永不写原目录）。

        只回写有译文的条目。失败保持原状态（可修复后重试）；成功进 done。
        """
        project = _require_project(ctx)
        info = project.info()
        entry_mod = _plugin_manager().get_entry(info.engine_id)
        entries = _all_translated(project.repo)
        try:
            raw = entry_mod.write_back(
                info.source_path, params["output_dir"], [_dump(e) for e in entries]
            )
            result = WriteBackResult.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 — 插件异常映射为项目错误
            raise RpcError(RpcErrorCode.PROJECT_ERROR, f"回写失败: {exc}") from exc
        project.set_state(ProjectState.writing_back)
        project.set_state(ProjectState.done)
        return _dump(result)

    @reg.register("plugins.list", EmptyParams)
    def plugins_list(params: dict[str, Any], ctx: dict[str, Any]) -> list[dict[str, Any]]:
        """已加载插件列表（含 disabled 与失败原因）。"""
        return [_dump(p) for p in _plugin_manager().infos()]

    # ---------- M3：providers（Provider 层） ----------

    @reg.register("providers.list", EmptyParams)
    def providers_list(params: dict[str, Any], ctx: dict[str, Any]) -> list[dict[str, Any]]:
        """可用翻译 Provider 列表（Provider 选择器渲染）。"""
        return [_dump(p) for p in _provider_manager().infos()]

    @reg.register("providers.test", ProviderTestParams)
    async def providers_test(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """Provider 连通性自检（async handler，serve_loop await；密钥可选覆盖）。

        base_url 传入 = 测试**未保存**的临时配置（UI 保存前测连通）；
        否则用已注册 Provider。
        """
        mgr = _provider_manager()
        try:
            result = await mgr.test(
                params["provider_id"], model=params.get("model"),
                api_key=params.get("api_key"), base_url=params.get("base_url"),
            )
        except KeyError as exc:
            raise RpcError(RpcErrorCode.PROVIDER_ERROR, str(exc)) from exc
        return _dump(result)

    @reg.register("providers.configure", ProviderConfigureParams)
    def providers_configure(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """接入/更新真实 Provider（DeepSeek/OpenAI/自定义 base_url + 模型 + key）。

        配置持久化 ~/.gametr/providers.json；api_key 可选（供 resolve_api_key）。
        模型 API 面板「添加 Provider」调用；翻译用 provider_id 引用。
        """
        mgr = _provider_manager()
        try:
            info = mgr.configure(**params)
        except Exception as exc:  # noqa: BLE001 — 配置失败映射为 Provider 错误
            raise RpcError(RpcErrorCode.PROVIDER_ERROR, f"配置失败: {exc}") from exc
        return _dump(info)

    @reg.register("providers.models", ProviderModelsParams)
    async def providers_models(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """获取 Provider 可用模型列表（UI「获取模型」按钮，自动填充模型下拉）。"""
        mgr = _provider_manager()
        try:
            models = await mgr.list_models(
                params["provider_id"], api_key=params.get("api_key"),
                base_url=params.get("base_url"),
            )
        except KeyError as exc:
            raise RpcError(RpcErrorCode.PROVIDER_ERROR, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — 网络/端点错误映射
            raise RpcError(RpcErrorCode.PROVIDER_ERROR, f"获取模型失败: {exc}") from exc
        return {"provider_id": params["provider_id"], "models": models}

    # ---------- M3：translate（任务管理） ----------

    @reg.register("translate.start", TranslateStartParams)
    async def translate_start(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """启动翻译任务：立即返回 task_id，后台协程逐批翻译 + progress 通知。

        - 断点续翻：只选未翻译条目（translation is null），已翻的自然跳过
        - overwrite_confirmed=True 才重译已确认（SQL 层守卫，防误覆盖）
        - api_key 规范路径 = 环境变量注入（RPC 日志已脱敏）
        """
        project = _require_project(ctx)
        info = project.info()
        try:
            provider = _provider_manager().get(params["provider_id"])
        except KeyError as exc:
            raise RpcError(RpcErrorCode.PROVIDER_ERROR, str(exc)) from exc
        entries = _select_translatable(project.repo, params)
        if not entries:
            raise RpcError(RpcErrorCode.PROJECT_ERROR, "没有可翻译条目（scope 内已全部翻译/已确认）")
        glossary_entries = project.repo.glossary_list()
        glossary_text = format_glossary(glossary_entries)
        task_store = TaskStore(project.conn)
        model = params.get("model") or provider.models[0]
        task = task_store.create(
            provider_id=params["provider_id"], model=model,
            style_id=params.get("style_id"),
            glossary_version=_glossary_version(glossary_entries),
            total=len(entries),
        )
        api_key = _provider_manager().resolve_api_key(params["provider_id"])
        protector = _plugin_manager().get_protector(info.engine_id)
        asyncio.get_running_loop().create_task(run_translate_task(
            task_id=task.task_id, task_store=task_store, project=project,
            entries=entries, provider=provider, model=model, api_key=api_key,
            glossary=glossary_text, protector=protector,
            notify=ctx.get("notify", lambda _rec: None),
        ))
        return _dump(task)

    @reg.register("translate.pause", TranslateTaskParams)
    def translate_pause(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """暂停任务（任务态→paused，流水线批边界停止；已翻批次保留）。"""
        return _dump(_control_task(ctx, params["task_id"], "paused"))

    @reg.register("translate.resume", TranslateTaskParams)
    def translate_resume(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """恢复任务（paused→running，幂等）。MVP：协程若已停止需重新 start 续翻。"""
        return _dump(_control_task(ctx, params["task_id"], "running"))

    @reg.register("translate.cancel", TranslateTaskParams)
    def translate_cancel(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """取消任务（任务态→cancelled，partial 结果保留进校对）。"""
        return _dump(_control_task(ctx, params["task_id"], "cancelled"))

    @reg.register("translate.status", TranslateStatusParams)
    def translate_status(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """查任务状态（task_id 缺省取最近任务，sidecar 重启后前端恢复）。"""
        return _dump(_get_task(ctx, params.get("task_id")))

    @reg.register("translate.stats", TranslateStatsParams)
    def translate_stats(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """用量统计（token；成本 MVP 为 0，M4 接单价配置）。"""
        task = _get_task(ctx, params.get("task_id"))
        stats = TaskStore(_require_project(ctx).conn).stats(task.task_id)
        if stats is None:
            raise RpcError(RpcErrorCode.PROJECT_ERROR, "任务用量不可用")
        return _dump(stats)

    @reg.register("translate.export", TranslateExportParams)
    def translate_export(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """导出翻译文件到指定路径（前端 save dialog 给路径）。

        其他用户下载同款游戏，导入此文件即可复用译文，无需重新翻译。
        格式含 format/version/engine_id（导入时校验同款游戏）。
        """
        project = _require_project(ctx)
        entries = _all_entries(project.repo)
        payload = {
            "format": "gametr-translation",
            "version": 1,
            "engine_id": project.info().engine_id,
            "count": len(entries),
            "entries": [
                {"locator": e.locator, "source": e.source, "translation": e.translation}
                for e in entries
            ],
        }
        path = Path(params["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"path": str(path), "count": len(entries)}

    @reg.register("translate.import", TranslateImportParams)
    def translate_import(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """从翻译文件导入译文（前端 open dialog 给路径）：按 locator 匹配。

        - locator 是匹配键（同款游戏文件结构相同 → locator 相同）
        - source 不一致 → warning（版本差异，仍按当前项目原文保留）
        - 待译条目导入后 → 机翻（人工编辑 → 已改）
        """
        project = _require_project(ctx)
        repo = project.repo
        try:
            data = json.loads(Path(params["path"]).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RpcError(RpcErrorCode.PROJECT_ERROR, f"翻译文件读取失败: {exc}") from exc
        if not isinstance(data, dict) or data.get("format") != "gametr-translation":
            raise RpcError(RpcErrorCode.PROJECT_ERROR, "不是有效的 GameTR 翻译文件")
        if data.get("engine_id") != project.info().engine_id:
            raise RpcError(RpcErrorCode.PROJECT_ERROR,
                           f"翻译文件来自其他引擎（{data.get('engine_id')}），不匹配当前项目")
        by_locator = {e.locator: e for e in _all_entries(repo)}
        imported = skipped = 0
        warnings: list[str] = []
        for item in data.get("entries") or []:
            locator = item.get("locator")
            translation = item.get("translation")
            if not isinstance(locator, str) or not isinstance(translation, str) or locator not in by_locator:
                skipped += 1
                continue
            entry = by_locator[locator]
            if item.get("source") != entry.source:
                warnings.append(f"{locator}: 原文与导入文件不一致，译文按当前项目写入")
            # 待译→机翻；已改/已确认保持（不覆盖人工状态）
            new_status = EntryStatus.MACHINE if entry.status == EntryStatus.PENDING else entry.status
            repo.update(entry.id, translation=translation, status=new_status)
            imported += 1
        return {"imported": imported, "skipped": skipped, "warnings": warnings}

    @reg.register("translate.retranslate_entries", TranslateRetranslateParams)
    async def translate_retranslate(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """单条/批量重译（带上下文——MVP 复用流水线 + 术语表）。

        改**后台任务**（与 translate.start 同构）：立即返回 task_id，后台翻译 +
        progress 通知。原来同步等待会把大重译卡过 Rust 30s 超时（实测 67 条重译超时）。
        """
        project = _require_project(ctx)
        info = project.info()
        repo = project.repo
        entries = [e for e in (repo.get(eid) for eid in params["ids"]) if e is not None]
        if not entries:
            raise RpcError(RpcErrorCode.PROJECT_ERROR, "条目不存在")
        provider_id = params.get("provider_id") or _default_provider_id()
        try:
            provider = _provider_manager().get(provider_id)
        except KeyError as exc:
            raise RpcError(RpcErrorCode.PROVIDER_ERROR, str(exc)) from exc
        glossary_entries = repo.glossary_list()
        glossary_text = format_glossary(glossary_entries)
        task_store = TaskStore(project.conn)
        model = params.get("model") or provider.models[0]
        task = task_store.create(
            provider_id=provider_id, model=model, style_id=params.get("style_id"),
            glossary_version=_glossary_version(glossary_entries),
            total=len(entries),
        )
        api_key = _provider_manager().resolve_api_key(provider_id)
        protector = _plugin_manager().get_protector(info.engine_id)
        asyncio.get_running_loop().create_task(run_translate_task(
            task_id=task.task_id, task_store=task_store, project=project,
            entries=entries, provider=provider, model=model, api_key=api_key,
            glossary=glossary_text, protector=protector,
            notify=ctx.get("notify", lambda _rec: None),
        ))
        return {"task_id": task.task_id}

    @reg.register("glossary.delete", GlossaryDeleteParams)
    def glossary_delete(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """删除术语（M4 术语表 CRUD 补齐）。"""
        repo = _require_project(ctx).repo
        return {"deleted": repo.glossary_delete(params["term"])}

    return reg


def _close_if_any(ctx: dict[str, Any]) -> None:
    """换项目前关闭旧连接，避免句柄泄漏。"""
    project = ctx.get("project")
    if isinstance(project, Project):
        project.close()
    ctx.pop("project", None)


# ---------- 插件管理器（模块级单例，测试可注入替换） ----------

_plugins: PluginManager | None = None


def _plugin_manager() -> PluginManager:
    """懒加载的插件管理器单例（进程内扫描一次，目录变化需重启 sidecar）。"""
    global _plugins
    if _plugins is None:
        _plugins = PluginManager()
    assert _plugins is not None
    return _plugins


def _set_plugin_manager(mgr: PluginManager | None) -> None:
    """测试注入：替换插件管理器（None 恢复默认扫描）。"""
    global _plugins
    _plugins = mgr


# ---------- Provider 管理器（模块级单例，测试可注入替换） ----------

_providers: ProviderManager | None = None


def _provider_manager() -> ProviderManager:
    """懒加载的 ProviderManager 单例（内置注册：mock + openai-compatible）。"""
    global _providers
    if _providers is None:
        _providers = ProviderManager()
    return _providers


def _set_provider_manager(mgr: ProviderManager | None) -> None:
    """测试注入：替换 Provider 管理器。"""
    global _providers
    _providers = mgr


def _build_entry(engine_id: str, raw: dict[str, Any], source_path: str) -> Entry:
    """插件 extract 的 dict -> 核心 Entry。

    稳定 ID 由核心计算（ir.entry_id），插件不依赖核心模型，只约定字段：
    locator/source 必填，context_json/warnings_json 可选（JSON 串）。
    """
    locator = raw.get("locator")
    source = raw.get("source")
    if not isinstance(locator, str) or not isinstance(source, str):
        raise RpcError(RpcErrorCode.PROJECT_ERROR, f"插件返回非法条目: {raw!r}")
    return Entry(
        id=entry_id(engine_id, locator, source),
        source=source,
        translation=None,
        status=EntryStatus.PENDING,
        locator=locator,
        context_json=raw.get("context_json"),
        warnings_json=raw.get("warnings_json"),
        updated_at=time.time(),
    )


def _all_translated(repo: Repo) -> list[Entry]:
    """分页取全部有译文的条目（write_back 只回写已翻译的）。"""
    out: list[Entry] = []
    page = 1
    while True:
        pg = repo.list_entries(page=page, page_size=2000)
        out.extend(e for e in pg.items if e.translation is not None)
        if page * pg.page_size >= pg.total:
            break
        page += 1
    return out


def _all_entries(repo: Repo) -> list[Entry]:
    """分页取全部条目（含未翻译；export/import 用）。"""
    out: list[Entry] = []
    page = 1
    while True:
        pg = repo.list_entries(page=page, page_size=2000)
        out.extend(pg.items)
        if page * pg.page_size >= pg.total:
            break
        page += 1
    return out


# ---------- translate.* 辅助 ----------


def _select_translatable(repo: Repo, params: dict[str, Any]) -> list[Entry]:
    """按 scope 选取可翻译条目（PENDING/MACHINE/EDITED，未翻译；跳过已确认）。

    - scope=all/file：分页全量 + file_path/status_filter 过滤
    - scope=selection：仅指定 ids
    - overwrite_confirmed=True：包含已确认且不跳过已翻译（重译语义）
    - 默认：跳过已翻译（断点续翻=只翻 translation is null）与已确认（守卫）
    """
    file_path = params.get("file_path")
    status = params.get("status_filter")
    all_entries: list[Entry] = []
    page = 1
    while True:
        pg = repo.list_entries(page=page, page_size=2000, file_path=file_path, status=status)
        all_entries.extend(pg.items)
        if page * pg.page_size >= pg.total:
            break
        page += 1
    if params.get("scope") == "selection":
        ids = set(params.get("ids") or [])
        all_entries = [e for e in all_entries if e.id in ids]
    if not params.get("overwrite_confirmed"):
        all_entries = [e for e in all_entries if e.status != EntryStatus.CONFIRMED]
        all_entries = [e for e in all_entries if not e.translation]
    return all_entries


def _glossary_version(glossary_entries: list[GlossaryEntry]) -> str:
    """术语表内容哈希（参与 cache_key：改术语 → 缓存失效）。"""
    raw = "\n".join(
        f"{g.term}|{g.translation}|{int(g.match_case or 0)}" for g in glossary_entries
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _default_provider_id() -> str:
    """默认 Provider：第一个已配置的；未配置报 PROVIDER_ERROR（无内置 mock）。"""
    infos = _provider_manager().infos()
    if not infos:
        raise RpcError(RpcErrorCode.PROVIDER_ERROR, "未配置 Provider（请先在「模型 API」添加）")
    return infos[0].provider_id


def _get_task(ctx: dict[str, Any], task_id: str | None) -> TranslateTask:
    """查任务（缺省最近任务）；无任务/不存在报 TRANSLATE_NOT_RUNNING。"""
    store = TaskStore(_require_project(ctx).conn)
    tid = task_id or store.recent_task_id()
    if tid is None:
        raise RpcError(RpcErrorCode.TRANSLATE_NOT_RUNNING, "无翻译任务")
    task = store.get(tid)
    if task is None:
        raise RpcError(RpcErrorCode.TRANSLATE_NOT_RUNNING, f"任务不存在: {tid}")
    return task


def _control_task(ctx: dict[str, Any], task_id: str, status: str) -> TranslateTask:
    """pause/resume/cancel：改任务态并返回任务。任务不存在报 TRANSLATE_NOT_RUNNING。"""
    store = TaskStore(_require_project(ctx).conn)
    task = store.get(task_id)
    if task is None:
        raise RpcError(RpcErrorCode.TRANSLATE_NOT_RUNNING, f"任务不存在: {task_id}")
    store.set_status(task_id, status)
    updated = store.get(task_id)
    assert updated is not None
    return updated
