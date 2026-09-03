"""翻译任务 runner（路线图 3.3）：后台协程编排 translate_entries + 任务态 + 进度通知。

translate.start 用 create_task 启动本协程；批边界检查任务态实现
pause/cancel（任务态 ≠ running 即停，不留半写状态）。
断点续翻：start 时只选未翻译条目（translation is null），已翻的自然跳过。
"""

from __future__ import annotations

import sys
import time
import traceback
from collections.abc import Callable
from typing import Any

from gt_core.providers.base import TranslationProvider
from gt_core.rpc.models import Entry, TranslateStatus
from gt_core.translate.pipeline import translate_entries
from gt_core.translate.tasks import TaskStore

NotifyFn = Callable[[dict[str, Any]], None]


def _progress(task_id: str, done: int, total: int, status: str,
              message: str | None = None, skipped: int = 0) -> dict[str, Any]:
    """构造 ProgressEvent 通知记录（status 含任务终态，前端一个 method 全覆盖）。"""
    return {
        "jsonrpc": "2.0", "method": "progress",
        "params": {
            "task_id": task_id, "phase": "translate", "done": done, "total": total,
            "status": status, "message": message, "ts": time.time(), "skipped": skipped,
        },
    }


async def run_translate_task(
    *,
    task_id: str,
    task_store: TaskStore,
    project: Any,
    entries: list[Entry],
    provider: TranslationProvider,
    model: str | None,
    api_key: str | None,
    glossary: str | None,
    protector: Any,
    notify: NotifyFn,
    cache_scope: tuple[str, str, str] | None = None,
) -> None:
    """后台翻译任务。任务态驱动：running 推进，paused/cancelled 批边界停止。

    cache_scope=(provider_id, model, glossary_version)：启用翻译缓存；重翻场景传 None
    （retranslate 要重新生成，缓存命中会返回上次的坏译文，见 translate_entries 文档）。
    """

    def should_cancel() -> bool:
        return not task_store.is_running(task_id)

    def on_progress(done: int, total: int, message: str | None) -> None:
        print(f"[task {task_id}] progress {done}/{total}", file=sys.stderr, flush=True)
        task_store.set_done(task_id, done)
        notify(_progress(task_id, done, total, "running", message=message))

    def on_usage(tokens_in: int, tokens_out: int) -> None:
        task_store.record_usage(task_id, provider.provider_id, model or "", tokens_in, tokens_out)

    print(f"[task {task_id}] start entries={len(entries)}", file=sys.stderr, flush=True)
    try:
        translated, warnings = await translate_entries(
            project=project, entries=entries, provider=provider,
            model=model, api_key=api_key, glossary=glossary,
            protector=protector, notify=on_progress, should_cancel=should_cancel,
            on_usage=on_usage, cache_scope=cache_scope,
        )
        print(f"[task {task_id}] done translated={translated}", file=sys.stderr, flush=True)
        task = task_store.get(task_id)
        final_done = task.total if task else len(entries)
        task_store.set_done(task_id, final_done)
        if task_store.is_running(task_id):
            task_store.set_status(task_id, TranslateStatus.done)
        notify(_progress(task_id, final_done, final_done, "done",
                         message=f"{translated} 条已翻译", skipped=final_done - translated))
    except Exception as exc:  # noqa: BLE001 — 任务级兜底，错误落任务态 + 通知
        traceback.print_exc()  # 诊断：后台任务崩因打到 stderr（serve 会 print 到日志/前端排障）
        task_store.set_status(task_id, TranslateStatus.error, str(exc))
        task = task_store.get(task_id)
        notify(_progress(task_id, task.done if task else 0, task.total if task else 0,
                         "error", message=str(exc)))
