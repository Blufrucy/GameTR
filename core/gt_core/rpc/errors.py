"""JSON-RPC 2.0 错误码与错误类型。

错误码表必须与 protocol/schema/rpc-methods.json 保持一致（协议是单一事实源）。
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any


class RpcErrorCode(IntEnum):
    """JSON-RPC 2.0 标准码 + 应用扩展码。"""

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    # 应用级错误码（-32000 区间，逐步补充；表见 rpc-methods.json）
    ENGINE_NOT_SUPPORTED = -32001
    NO_PROJECT = -32002
    PROJECT_ERROR = -32003


class RpcError(Exception):
    """携带错误码的异常；服务器捕获后转为 JSON-RPC error 响应。"""

    def __init__(self, code: RpcErrorCode, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data
