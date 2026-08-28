"""stdio 逐行 JSON-RPC 2.0 服务（NDJSON 帧）。

设计决策见 ADR-0002：
- stdin/stdout 一行一个 JSON-RPC 2.0 消息，行尾统一 \\n
- stdout 只写协议响应；日志一律走 stderr 与日志文件，保证协议通道纯净
- 编码强制 UTF-8：Windows 中文区域默认 cp936，游戏文本多为中日文，乱码会毁掉协议
- M0 同步串行处理；M3 流水线并发时在 MethodRegistry 之上扩展 asyncio 层
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from pydantic import BaseModel, ValidationError

from gt_core.rpc.errors import RpcError, RpcErrorCode

# 方法签名：(params, context) -> result
Handler = Callable[[dict[str, Any], dict[str, Any]], Any]


class MethodRegistry:
    """方法注册表：方法名 -> 处理器，装饰器注册。

    register(name, schema)：schema 为 pydantic 模型时，进入方法前做参数校验
    （校验失败 -> INVALID_PARAMS）。校验通过后参数转回 dict 再交给处理器。
    """

    def __init__(self) -> None:
        self._methods: dict[str, Handler] = {}
        self._schemas: dict[str, type[BaseModel]] = {}
        self._stopped = False

    def register(self, name: str, schema: type[BaseModel] | None = None) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            self._methods[name] = fn
            if schema is not None:
                self._schemas[name] = schema
            return fn

        return deco

    @property
    def stopped(self) -> bool:
        return self._stopped

    def request_shutdown(self) -> None:
        """置停止标志，serve_stdio 主循环在下一轮退出。"""
        self._stopped = True

    def handle(self, req: dict[str, Any], ctx: dict[str, Any]) -> Any:
        method = req.get("method")
        if not isinstance(method, str):
            raise RpcError(RpcErrorCode.INVALID_REQUEST, "method 必须为字符串")
        handler = self._methods.get(method)
        if handler is None:
            raise RpcError(RpcErrorCode.METHOD_NOT_FOUND, f"未知方法: {method!r}")
        params = req.get("params") or {}
        if not isinstance(params, dict):
            raise RpcError(RpcErrorCode.INVALID_PARAMS, "params 必须为对象")
        schema = self._schemas.get(method)
        if schema is not None:
            try:
                # 只保留调用方显式提供的字段；未传字段让 handler 用默认
                params = schema.model_validate(params).model_dump(exclude_unset=True)
            except ValidationError as exc:
                detail = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
                raise RpcError(RpcErrorCode.INVALID_PARAMS, f"参数校验失败: {detail}") from exc
        return handler(params, ctx)


def serve_stdio(
    registry: MethodRegistry,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    rpc_log_dir: Path | None = None,
) -> None:
    """阻塞式主循环：逐行读 stdin、写响应到 stdout。

    stdin/stdout 可注入（测试用）；默认取 sys.stdin/sys.stdout。
    """
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    _prepare_streams(stdin, stdout)
    ctx: dict[str, Any] = {"started_at": _now()}
    logger = _RpcLogger(rpc_log_dir)
    try:
        while not registry.stopped:
            line = stdin.readline()
            if not line:
                break  # EOF：GUI 进程已退出，或管道关闭
            line = line.strip()
            if not line:
                continue
            logger.log({"t": "req", "line": line})
            resp = _process_line(line, registry, ctx)
            if resp is not None:
                resp_line = json.dumps(resp, ensure_ascii=False)
                logger.log({"t": "resp", "line": resp_line})
                stdout.write(resp_line + "\n")
                stdout.flush()
    finally:
        logger.close()


def _process_line(line: str, registry: MethodRegistry, ctx: dict[str, Any]) -> dict[str, Any] | None:
    """单行请求 -> 响应字典；通知（无 id）返回 None 不应答。纯函数，便于单测。

    注意：通知出错也返回 None（JSON-RPC 2.0 规定「绝不能回复通知」，review
    修复——此前异常分支无条件回 error，会打乱按 id 配对的响应流）。
    """
    try:
        req = json.loads(line)
    except json.JSONDecodeError as exc:
        return _error(None, RpcErrorCode.PARSE_ERROR, f"JSON 解析失败: {exc}")
    if not isinstance(req, dict) or req.get("jsonrpc") != "2.0" or not isinstance(req.get("method"), str):
        return _error(_req_id(req), RpcErrorCode.INVALID_REQUEST, "请求必须是 JSON-RPC 2.0 对象且含 method")
    is_notification = "id" not in req
    try:
        result = registry.handle(req, ctx)
    except RpcError as exc:
        if is_notification:
            return None
        return _error(_req_id(req), exc.code, exc.message, exc.data)
    except Exception as exc:  # noqa: BLE001 — 兜底，避免协议通道被打断
        if is_notification:
            return None
        return _error(_req_id(req), RpcErrorCode.INTERNAL_ERROR, f"内部错误: {exc}")
    if is_notification:
        return None
    return {"jsonrpc": "2.0", "id": req["id"], "result": result}


def _req_id(req: Any) -> Any:
    return req.get("id") if isinstance(req, dict) else None


def _error(req_id: Any, code: RpcErrorCode, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": int(code), "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _prepare_streams(stdin: TextIO, stdout: TextIO) -> None:
    """强制 UTF-8 编码与 \\n 行尾，规避 Windows 中文区域默认编码（cp936）与 CRLF。"""
    if hasattr(stdin, "reconfigure"):
        stdin.reconfigure(encoding="utf-8", newline="\n", errors="replace")
    if hasattr(stdout, "reconfigure"):
        stdout.reconfigure(encoding="utf-8", newline="\n")


def _now() -> float:
    return datetime.now().timestamp()


class _RpcLogger:
    """RPC 全量日志：请求/响应逐行落 logs/rpc-*.ndjson（无遥测产品的重要排障手段）。"""

    def __init__(self, log_dir: Path | None) -> None:
        self._fh = None
        if log_dir is None:
            log_dir = Path("logs")
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            self._fh = (log_dir / f"rpc-{datetime.now():%Y%m%d-%H%M%S}.ndjson").open(
                "w", encoding="utf-8", newline="\n"
            )
        except OSError:
            self._fh = None  # 日志失败不阻断主流程

    def log(self, record: dict[str, Any]) -> None:
        if self._fh is not None:
            self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
