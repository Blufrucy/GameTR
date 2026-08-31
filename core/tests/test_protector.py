"""RPGMV 占位符保护器 fuzz 测试（路线图 2.2：随机嵌套占位符往返不丢失）。

用固定种子确定性生成含随机占位符的文本，protect → restore 必须精确还原。
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

# 插件目录（protector.py 在 plugins/rpgmv/）
_PLUGIN_DIR = Path(__file__).resolve().parent.parent.parent / "plugins" / "rpgmv"
sys.path.insert(0, str(_PLUGIN_DIR))
from protector import has_protected, protect, restore  # noqa: E402

# 可生成的占位符片段（覆盖全部保护集合）
_PLACEHOLDERS = [
    "\\C[3]", "\\I[5]", "\\N[1]", "\\P[2]", "\\V[7]", "\\V[10]",
    "\\G", "\\{", "\\}", "\\.", "\\^", "\\|", "\\!",
]
_TEXT_POOL = "あいうえお勇者魔王世界の冒険ABCxyz123 ！？、。\\n"


def _random_text(rng: random.Random) -> str:
    """随机拼一段含 0..8 个随机占位符的文本（含相邻/重复，模拟真实乱序）。"""
    parts: list[str] = []
    for _ in range(rng.randint(1, 20)):
        if rng.random() < 0.3:  # 30% 概率插入一个占位符
            parts.append(rng.choice(_PLACEHOLDERS))
        else:
            parts.append("".join(rng.choice(_TEXT_POOL) for _ in range(rng.randint(1, 8))))
    return "".join(parts)


def test_roundtrip_1000_random_texts() -> None:
    rng = random.Random(20260828)  # 固定种子：确定性，CI 可复现
    for _ in range(1000):
        text = _random_text(rng)
        protected, tokens = protect(text)
        assert restore(protected, tokens) == text, f"往返丢失: {text!r}"


def test_protect_indices_consecutive() -> None:
    text = "\\N[1]こんにちは\\N[1]\\G勇者"
    protected, tokens = protect(text)
    assert protected == "⟦0⟧こんにちは⟦1⟧⟦2⟧勇者"
    assert tokens == ["\\N[1]", "\\N[1]", "\\G"]
    # 相同占位符可重复出现，各自独立编号


def test_restore_detects_ai_mangled() -> None:
    """AI 删掉/改了占位符 → restore 应抛错（AI 破坏检测）。"""
    protected, tokens = protect("\\N[1]勇者")
    assert protected == "⟦0⟧勇者"
    with pytest.raises(ValueError):
        restore("⟦0⟧⟦9⟧", tokens)  # 未知索引 9（AI 幻觉了新哨兵）


def test_has_protected_flag() -> None:
    protected, _ = protect("\\Gこんにちは")
    assert has_protected(protected)
    assert not has_protected("こんにちは")
    assert not has_protected(restore(protected, ["\\G"]))


def test_lone_backslash_passes_through() -> None:
    """非占位符的反斜杠（如 \\n 转义文本）不得被保护。"""
    text = "これは\\n改行を含む"
    protected, tokens = protect(text)
    assert protected == text  # \\n 不匹配占位符，原样保留
    assert tokens == []


def test_adjacent_placeholders() -> None:
    text = "\\N[1]\\N[1]\\G\\C[3]"
    protected, tokens = protect(text)
    assert tokens == ["\\N[1]", "\\N[1]", "\\G", "\\C[3]"]
    assert restore(protected, tokens) == text


def test_plugin_brace_marker_whole_protected() -> None:
    """第三方插件双花括号标记 \\{{...\\}}（如 \\{{ANIMATIONS by:\\}}）整体保护。

    AI 不得翻译/改动内部（插件解析依据），否则游戏显示/解析失败（真实工程实测坑）。
    """
    text = r"\{{ANIMATIONS by:\}} こんにちは \{{MUSIC by:\}}"
    protected, tokens = protect(text)
    # 两个标记各整体成一个 token，内部文本不被拆开
    assert tokens == [r"\{{ANIMATIONS by:\}}", r"\{{MUSIC by:\}}"]
    # 标记整体替换为哨兵，中间普通文本保留
    assert protected == "⟦0⟧ こんにちは ⟦1⟧"
    assert restore(protected, tokens) == text
    # 保护后无裸标记（AI 看不到内部，不会翻译/改动）
    assert "ANIMATIONS" not in protected


def test_plugin_angle_tag_and_bracket_control() -> None:
    """尖括号插件标签（<c:002,160,0>）+ 带括号控制符（\\pop[0]）整体保护。

    Galv/VisuStella 插件控制符，AI 改动会破坏插件解析（真实工程实测）。
    """
    text = r"<c:002,160,0> galvs timed \pop[0] message <KNHShadow:10,-25,90>"
    protected, tokens = protect(text)
    assert tokens == [r"<c:002,160,0>", r"\pop[0]", r"<KNHShadow:10,-25,90>"]
    assert "c:002" not in protected and "pop" not in protected  # 内部不暴露给 AI
    assert restore(protected, tokens) == text


def test_emoji_angle_not_mistaken() -> None:
    """emoji 里的尖括号（(>_<)、<3）不是插件标签，不得误伤。"""
    text = "(>_<)┌(>_<)┘ <3 ありがとう"
    protected, tokens = protect(text)
    assert tokens == []  # 无插件标签，全保普通文本
    assert protected == text
