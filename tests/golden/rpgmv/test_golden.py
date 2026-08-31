"""黄金样本三测试（M2 发布闸门，路线图 2.3）。

1. 快照：extract(样本) == expected.json（程序化生成样本 → 提取结果逐字段一致）
2. 空翻译往返：extract → translation=source 原样回写 → data 文件二进制 diff=0
3. 带翻译往返：extract → 改译文 → 回写 → 重新 extract → 译文一致

样本由 make_sample.generate 程序化生成（版权干净、可重建），跑在 tmp_path。
真实引擎验证在 tests/spikes/spike2_rpgmv/（2026-08-28，17580 字符串）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gt_core.plugin import PluginManager

# 测试文件所在目录 = tests/golden/rpgmv
_HERE = Path(__file__).resolve().parent
# 仓库根 = 上上级（tests/golden -> tests -> 根）
_REPO = _HERE.parent.parent.parent
_PLUGINS = str(_REPO / "plugins")
_EXPECTED = json.loads((_HERE / "expected.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def adapter():
    mgr = PluginManager([_PLUGINS])
    entry = mgr.get_entry("rpgmv")
    assert entry is not None
    return entry


@pytest.fixture()
def game(tmp_path):
    """程序化生成黄金样本到 tmp，返回游戏根。"""
    import sys
    sys.path.insert(0, str(_HERE))
    from make_sample import generate
    return generate(tmp_path / "game")


def _data_files(root: Path) -> dict[str, bytes]:
    """游戏根下全部 data 文件的 {相对路径: 原始字节}（www/data 或 data）。"""
    for rel in ("www/data", "data"):
        base = root / rel
        if base.is_dir():
            return {str(p.relative_to(root)): p.read_bytes() for p in sorted(base.glob("*.json"))}
    return {}


def _entries(adapter, game: Path) -> list[dict]:
    """extract 并把 context_json 解析开（快照对比需要结构化比较）。"""
    out = []
    for e in adapter.extract(str(game)):
        e = dict(e)
        e["context_json"] = json.loads(e["context_json"])
        out.append(e)
    return out


# ---------- 1. 快照 ----------

def test_snapshot_matches_expected(adapter, game):
    got = _entries(adapter, game)
    exp = [dict(e) for e in _EXPECTED]
    for e in exp:
        e["context_json"] = json.loads(e["context_json"])
    assert got == exp


# ---------- 2. 空翻译往返（diff=0） ----------

def test_empty_roundtrip_zero_diff(adapter, game, tmp_path):
    entries = adapter.extract(str(game))
    for e in entries:
        e["translation"] = e["source"]  # 恒等：不改任何文本
    out = tmp_path / "out"
    res = adapter.write_back(str(game), str(out), entries)
    assert res["written_count"] == len(entries)
    assert res["warning_count"] == 0
    # 输出 data 文件与原文逐字节一致（格式免疫，零差异）
    assert _data_files(out) == _data_files(game)


# ---------- 3. 带翻译往返 ----------

def test_translated_roundtrip(adapter, game, tmp_path):
    entries = adapter.extract(str(game))
    # 挑三类各一条改译文：数据库字段 / 401 拼接段 / 选项
    picks: dict[str, dict] = {}
    for e in entries:
        if e["locator"] == "Actors.json::$[0].name":
            picks["actor"] = e
        elif e["source"].startswith("こんにちは"):
            picks["message"] = e  # 连续 401 拼接成段落的 Entry
        elif e["locator"].endswith(".parameters[0][0]") and e["source"] == "はい":
            picks["choice"] = e
    assert len(picks) == 3, [e["locator"] for e in entries]

    translations = {
        "actor": "勇者爱丽丝",
        "message": "你好，\\N[1]勇者！\n下一行也是同样的消息。\\C[2]红色",
        "choice": "好耶",
    }
    for e in entries:
        # 选中条目用译文，其余恒等（原样回写，保证只验证被改的部分）
        e["translation"] = translations.get(next(
            (k for k, pick in picks.items() if pick["locator"] == e["locator"]), ""
        ), e["source"])

    out = tmp_path / "out"
    res = adapter.write_back(str(game), str(out), entries)
    assert res["warning_count"] == 0, res["message"]

    # 重新 extract 输出，译文应已生效（按 locator 定位）
    re_entries = _entries(adapter, out)
    remap = {e["locator"]: e["source"] for e in re_entries}
    for key, e in picks.items():
        assert remap.get(e["locator"]) == translations[key], (
            f"{key}: {remap.get(e['locator'])!r} != {translations[key]!r}"
        )
