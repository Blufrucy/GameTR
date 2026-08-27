"""核心 RPC 方法注册。

M0 首批方法（路线图 1.5 骨架）：core.ping / core.shutdown / core.log_level。
其余方法（project.*、entries.*、glossary.*）于 M1 落地；方法清单见
protocol/schema/rpc-methods.json。
"""

from __future__ import annotations

import os
import time
from typing import Any

import gt_core
from gt_core.rpc.errors import RpcError, RpcErrorCode
from gt_core.rpc.server import MethodRegistry

_LOG_LEVELS = ("debug", "info", "warn", "error")


def register_core_methods() -> MethodRegistry:
    reg = MethodRegistry()

    @reg.register("core.ping")
    def ping(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """健康检查：GUI 启动时以 500ms 间隔重试直到成功（路线图 1.3）。"""
        return {
            "pong": True,
            "version": gt_core.__version__,
            "pid": os.getpid(),
            "ts": time.time(),
        }

    @reg.register("core.shutdown")
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

    return reg
