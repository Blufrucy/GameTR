"""IR 数据模型工具（路线图 1.2）。

pydantic 模型本身由 protocol 生成（`gt_core/rpc/models.py`），本模块提供：
- 稳定 ID 算法：同一条文本重新提取 ID 不变、文本变了 ID 变（路线图公式）
- locator 标准化与序列化契约（ADR-0003：核心只存不解析，但序列化格式要稳定）

用法：`from gt_core.ir import entry_id`；模型从 `gt_core.rpc.models` 导入。
"""

from __future__ import annotations

import hashlib

# 分隔符用不可见字符（\x1f = US），避免与文本内容冲突
_ID_SEP = "\x1f"


def normalize_locator(locator: str) -> str:
    """Locator 标准化：去首尾空白，其余原样。

    ADR-0003 中 locator 对核心不透明，标准化只做不改变语义的整理，
    不能做任何解析（插件负责生成与解析）。
    """
    return locator.strip()


def entry_id(engine_id: str, locator: str, source: str) -> str:
    """稳定 ID：sha1(engine_id + locator标准化串 + source)[:16]。

    保证（M1 单元测试覆盖）：
    - 同一条文本（同 engine/locator/source）重新提取 ID 不变
    - 文本变了 ID 变（任何位置变化都变）
    - 不同引擎/位置的同文本 ID 不同（避免跨项目串 ID）

    说明：路线图原公式写「source前256字符」，但截断会让「共享 256 前缀的
    两条不同长文本」得到相同 ID，upsert 时按 id 覆盖把已确认数据静默重置
    （review 实测复现）。故直接用完整 source 哈希，sha1 处理任意长度无成本差异。
    """
    loc = normalize_locator(locator)
    raw = f"{engine_id}{_ID_SEP}{loc}{_ID_SEP}{source}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
