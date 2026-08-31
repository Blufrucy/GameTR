"""RPGMV 占位符保护器（路线图 2.2，M3 流水线前置）。

RPG Maker 控制符（`\\N[1]` 角色名、`\\V[3]` 变量、`\\C[2]` 颜色…）是游戏语法，
AI 翻译时一旦改动就破坏游戏显示。保护器把占位符换成 `⟦0⟧`… 再交给 AI，
译文回写前还原为原占位符。

保护集合（计划 2.2 + 插件扩展）：
  \\C[n] \\I[n] \\N[n] \\P[n] \\V[n]   （标准带编号；\\[A-Za-z]+[n] 一并覆盖）
  \\pop[0] 等任意 \\字母[n] 控制符       （第三方插件：Galv 弹窗等）
  \\G \\{ \\} \\. \\^ \\| \\!         （单字符控制符）
  \\{{...\\}}                          （双花括号插件标记，如 \\{{ANIMATIONS by:\\}}）
  <c:002,160,0> 等尖括号标签            （Galv/VisuStella 插件：颜色/动画/标签，整体保护）

正确性：protect 输出 `⟦i⟧` 连续无重复；restore 用原列表精确还原。
任意占位符被 AI 改动/删除会在 restore 时报错（AI 破坏检测）。
"""

from __future__ import annotations

import re

# 保护顺序（避免互相吞）：
# 1 双花括号标记（内含 \{ \} 单字符）
# 2 带括号字母控制符（标准 \\C[3] + 插件 \\pop[0]）
# 3 单字符控制符
# 4 尖括号插件标签（以英文字母开头；不误伤 emoji 如 (>_<)、<3 数字）
_PLACEHOLDER_RE = re.compile(
    r"\\\{\{.*?\\\}\}"
    r"|\\[A-Za-z]+\[\d+\]"
    r"|\\[G{}^.!|]"
    r"|<[A-Za-z][A-Za-z0-9_\-]*(:[^>]*)?>",
    re.DOTALL,
)
# 保护后占位符（U+27E6/27E7 数学括号，天然不会被游戏/普通文本使用）
_SENTINEL_RE = re.compile(r"⟦(\d+)⟧")


def protect(text: str) -> tuple[str, list[str]]:
    """占位符 → ⟦i⟧。返回 (保护后文本, 原占位符列表)。"""
    tokens: list[str] = []

    def _repl(m: re.Match[str]) -> str:
        tokens.append(m.group(0))
        return f"⟦{len(tokens) - 1}⟧"

    return _PLACEHOLDER_RE.sub(_repl, text), tokens


def restore(text: str, tokens: list[str]) -> str:
    """⟦i⟧ → 原占位符。索引越界（AI 破坏/插入陌生哨兵）抛 ValueError。"""
    def _repl(m: re.Match[str]) -> str:
        idx = int(m.group(1))
        if idx >= len(tokens):
            raise ValueError(f"占位符索引越界: ⟦{idx}⟧（tokens 仅 {len(tokens)} 个）")
        return tokens[idx]

    return _SENTINEL_RE.sub(_repl, text)


def has_protected(text: str) -> bool:
    """是否含未还原的占位符哨兵（校验用：AI 输出残留 ⟦i⟧ 即判失败）。"""
    return _SENTINEL_RE.search(text) is not None
