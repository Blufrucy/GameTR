"""流水线纯函数阶段（路线图 3.1）：Protector / Validator / Restorer。

不依赖项目/网络，可单测。占位符保护器由插件提供（feature-detect，见 PluginManager
get_protector）——语法是引擎域，核心只按契约调用；缺省时用身份保护器（原样 passthrough）。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# 占位符哨兵（与 plugins/rpgmv/protector.py 同约定，引擎无关）
_PH_RE = re.compile(r"⟦(\d+)⟧")

# 保护器形状：(protect, restore, has_protected?)
Protector = tuple[Callable[[str], tuple[str, list[str]]], Callable[[str, list[str]], str],
                  Callable[[str], bool] | None]

def _identity_protect(s: str) -> tuple[str, list[str]]:
    return (s, [])


def _identity_restore(s: str, _tokens: list[str]) -> str:
    return s


def get_protector_fns(protector: Protector | None) -> tuple[
    Callable[[str], tuple[str, list[str]]], Callable[[str, list[str]], str],
    Callable[[str], bool],
]:
    """补全保护器三元组；缺省时 has_protected 用序列比对实现。"""
    if protector is not None:
        p, r, h = protector
        return (p, r, h if h is not None else has_placeholders)
    return (_identity_protect, _identity_restore, has_placeholders)


def ph_sequence(text: str) -> list[str]:
    """占位符编号序列（数量+顺序+编号，Validator 比对用）。"""
    return _PH_RE.findall(text)


def has_placeholders(text: str) -> bool:
    return bool(_PH_RE.search(text))


@dataclass(frozen=True)
class ProtectedItem:
    """保护后的条目：id 关联原条目，tokens 用于还原。"""

    id: str
    protected: str
    tokens: list[str]


def protect_all(entries: list[Any],
                protect: Callable[[str], tuple[str, list[str]]]) -> list[ProtectedItem]:
    """Protector：逐条 source → ⟦n⟧ 哨兵。"""
    return [ProtectedItem(id=e.id, protected=t, tokens=ts)
            for e in entries for t, ts in [protect(e.source)]]


def validate_result(src_protected: str, ai_translation: str,
                    has_ph: Callable[[str], bool]) -> tuple[bool, str]:
    """Validator：占位符序列一致 + 非空 + 非原文直返（漏译检测）。

    返回 (ok, warning)。重试 1 次仍失败由流水线标 warning 保留 AI 结果。
    """
    ai = ai_translation if isinstance(ai_translation, str) else ""
    if not ai.strip():
        return False, "空译文"
    if ai.strip() == src_protected.strip():
        return False, "漏译（返回原文）"
    if ph_sequence(ai) != ph_sequence(src_protected):
        return False, f"占位符序列不一致: {ph_sequence(ai)!r} != {ph_sequence(src_protected)!r}"
    return True, ""


def restore_all(translations: dict[str, str], items: list[ProtectedItem],
                restore: Callable[[str, list[str]], str]) -> list[tuple[str, str]]:
    """Restorer：把通过校验的 ⟦n⟧ 译文还原为原占位符。返回 [(entry_id, 译文)]。"""
    out: list[tuple[str, str]] = []
    for item in items:
        ai = translations.get(item.id)
        if ai is None:
            continue
        out.append((item.id, restore(ai, item.tokens)))
    return out
