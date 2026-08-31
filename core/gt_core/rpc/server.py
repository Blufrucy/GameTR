"""stdio 逐行 JSON-RPC 2.0 服务（NDJSON 帧）。

设计决策见 ADR-0002：
- stdin/stdout 一行一个 JSON-RPC 2.0 消息，行尾统一 \\n
- stdout 只写协议响应；日志一律走 stderr 与日志文件，保证协议通道纯净
- 编码强制 UTF-8：Windows 中文区域默认 cp936，游戏文本多为中日文，乱码会毁掉协议
- M0 同步串行处理；M3 流水线并发时在 MethodRegistry 之上扩展 asyncio 层
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import sys
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from pydantic import BaseModel, ValidationError

from gt_core.rpc.errors import RpcError, RpcErrorCode

# 方法签名：(params, context) -> result
Handler = Callable[[dict[str, Any], dict[str, Any]], Any]

# ---------- RPC 日志脱敏（无遥测产品最易被共享，key 明文落盘即泄露面） ----------
# 命中规则：字段名小写化后精确等于或以下划线后缀结尾（api_key / access_token ...）
_SENSITIVE_KEYS = {"api_key", "apikey", "authorization", "token", "secret", "password"}
_MASKED = "***"
# 兜底：解析失败的行（如 header 风格）掩码 sk-xxx 长串（保留前 4 位便于对账）
_SK_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{4})[A-Za-z0-9_\-]+")


def _is_sensitive_key(key: str) -> bool:
    kl = key.lower().replace("-", "_")
    return any(kl == s or kl.endswith("_" + s) for s in _SENSITIVE_KEYS)


def mask_sensitive(obj: Any) -> Any:
    """递归掩码敏感字段值（api_key/token/secret...）；字符串值若本身是 JSON 再递归。

    record 结构如 {"t":"req","line":"{...api_key...}"}：line 是整行 JSON 字符串，
    递归时再解析一次，确保参数里的密钥也被掩码。
    """
    if isinstance(obj, dict):
        return {
            k: (_MASKED if _is_sensitive_key(k) and v is not None else mask_sensitive(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [mask_sensitive(v) for v in obj]
    if isinstance(obj, str):
        try:
            inner = json.loads(obj)
            if isinstance(inner, (dict, list)):
                return json.dumps(mask_sensitive(inner), ensure_ascii=False)
        except json.JSONDecodeError:
            pass
        return _SK_RE.sub(rf"\g<1>{_MASKED}", obj)
    return obj


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
        """同步入口（test_rpc 等纯函数测试用）：handler 为协程时返回未 await 的 coroutine。"""
        return self._handle(req, ctx)

    async def handle_async(self, req: dict[str, Any], ctx: dict[str, Any]) -> Any:
        """异步入口（serve_loop 用）：handler 为协程时 await（M3 providers.test/translate.start）。"""
        result = self._handle(req, ctx)
        if inspect.iscoroutine(result):
            result = await result
        return result

    def _handle(self, req: dict[str, Any], ctx: dict[str, Any]) -> Any:
        """方法解析 + 参数校验 + 调用 handler（同步返回；协程 handler 返回 coroutine）。"""
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
    """阻塞式主循环：逐行读 stdin、写响应到 stdout。同步签名（asyncio.run 内部驱动）。

    stdin/stdout 可注入（测试用）；默认取 sys.stdin/sys.stdout。

    M3 并发化（ADR-0002 预留）：主循环跑在 asyncio 事件循环——stdin 读行走
    executor 线程（不阻塞循环），stdout 由**单 writer 协程**从队列串行写出
    （响应与 progress 通知共用该 writer，保证 NDJSON 帧原子性、通知不打断响应流）。
    同步 handler 直接在循环线程执行（SQLite 毫秒级）；长任务（translate 流水线）
    由 handler 里 create_task 启动后台协程，经 ctx['notify'] 发通知。
    """
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    _prepare_streams(stdin, stdout)
    _prepare_stderr()  # 诊断输出（traceback）编码防御
    ctx: dict[str, Any] = {"started_at": _now()}
    logger = _RpcLogger(rpc_log_dir)
    asyncio.run(_serve_loop(registry, stdin, stdout, ctx, logger))


def _prepare_stderr() -> None:
    """stderr 编码防御：后台任务 traceback 可能含中文，ascii 编码会二次抛错。"""
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


async def _serve_loop(
    registry: MethodRegistry,
    stdin: TextIO,
    stdout: TextIO,
    ctx: dict[str, Any],
    logger: _RpcLogger,
) -> None:
    """asyncio 事件循环：stdin reader（executor）→ 处理 → 单 writer 串行写 stdout。

    ctx 注入：
    - loop：后台任务（translate 流水线）用它 create_task 检查取消
    - notify(record)：后台任务发通知（put_nowait 进 writer 队列，帧原子）
    """
    loop = asyncio.get_running_loop()
    out_queue: asyncio.Queue[str | None] = asyncio.Queue()
    ctx["loop"] = loop
    ctx["notify"] = lambda record: out_queue.put_nowait(
        json.dumps(record, ensure_ascii=False) + "\n"
    )
    writer = asyncio.create_task(_stdout_writer(out_queue, stdout))
    try:
        while not registry.stopped:
            line = await loop.run_in_executor(None, stdin.readline)
            if not line:
                break  # EOF：GUI 进程已退出，或管道关闭
            line = line.strip()
            if not line:
                continue
            logger.log({"t": "req", "line": line})
            resp = await _process_line_async(line, registry, ctx)
            if resp is not None:
                resp_line = json.dumps(resp, ensure_ascii=False)
                logger.log({"t": "resp", "line": resp_line})
                out_queue.put_nowait(resp_line + "\n")
    finally:
        # 先等后台任务（translate 流水线）完成：通知经 notify put 进队列；
        # **再关 writer**——顺序反了 writer 先退出，后台任务的通知堆积丢失。
        # pending 必须排除 writer 任务（它等在队列哨兵上，gather 它会死锁）。
        current = asyncio.current_task()
        pending = [t for t in asyncio.all_tasks(loop)
                   if t is not current and t is not writer and not t.done()]
        if pending:
            results = await asyncio.gather(*pending, return_exceptions=True)
            # 诊断：后台任务异常静默会丢根因，打 stderr
            for r in results:
                if isinstance(r, BaseException):
                    traceback.print_exception(type(r), r, r.__traceback__, file=sys.stderr)
        await out_queue.put(None)  # EOF 哨兵：writer 消费完剩余通知后退出
        await writer
        logger.close()


async def _stdout_writer(out_queue: asyncio.Queue[str | None], stdout: TextIO) -> None:
    """单 writer 协程：从队列取行串行写 stdout（响应 + 通知共用，帧原子）。"""
    while True:
        item = await out_queue.get()
        if item is None:
            break
        _emit(stdout, item)


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


async def _process_line_async(line: str, registry: MethodRegistry, ctx: dict[str, Any]) -> dict[str, Any] | None:
    """异步版 _process_line：await coroutine handler（M3 translate/providers）。

    错误语义与同步版完全一致（通知出错不应答；错误码映射；notify 不打断响应流）。
    """
    try:
        req = json.loads(line)
    except json.JSONDecodeError as exc:
        return _error(None, RpcErrorCode.PARSE_ERROR, f"JSON 解析失败: {exc}")
    if not isinstance(req, dict) or req.get("jsonrpc") != "2.0" or not isinstance(req.get("method"), str):
        return _error(_req_id(req), RpcErrorCode.INVALID_REQUEST, "请求必须是 JSON-RPC 2.0 对象且含 method")
    is_notification = "id" not in req
    try:
        result = await registry.handle_async(req, ctx)
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
        stdout.reconfigure(encoding="utf-8", newline="\n", errors="replace")


def _emit(stdout: TextIO, text: str) -> None:
    """写一行到 stdout，**优先用 buffer 写 UTF-8 bytes**。

    规避 PyInstaller console=False / Windows 下 TextIOWrapper 编码层可能保持
    ascii（reconfigure 不总生效）导致的 UnicodeEncodeError——buffer 是 raw
    bytes 流，无编码层，任何环境中文/日文无损写出（实测坑，见 AGENTS.md）。
    """
    buf = getattr(stdout, "buffer", None)
    if buf is not None:
        buf.write(text.encode("utf-8", errors="replace"))
        buf.flush()
    else:
        try:
            stdout.write(text)
            stdout.flush()
        except UnicodeEncodeError:
            # 无 buffer 且编码层拒绝：兜底用可编码替代
            stdout.write(text.encode("utf-8", errors="replace").decode("ascii", errors="replace"))
            stdout.flush()


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
            # 敏感字段（api_key/token/...）掩码后落盘，防止密钥进排障日志（脱敏单点）
            self._fh.write(json.dumps(mask_sensitive(record), ensure_ascii=False) + "\n")
            self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
