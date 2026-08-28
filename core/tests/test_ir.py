"""IR 模块单测：稳定 ID 算法与 locator 契约（路线图 1.2 的硬性要求）。"""

from gt_core.ir import entry_id, normalize_locator


class TestEntryId:
    """稳定 ID：同文本重提取不变、文本变了 ID 变、不同位置不同 ID。"""

    def test_same_text_same_id(self):
        id1 = entry_id("rpgmv", "$.events[0].list[1].params[0]", "こんにちは")
        id2 = entry_id("rpgmv", "$.events[0].list[1].params[0]", "こんにちは")
        assert id1 == id2

    def test_source_change_changes_id(self):
        a = entry_id("rpgmv", "loc1", "こんにちは")
        b = entry_id("rpgmv", "loc1", "こんにちは。")
        assert a != b  # 文本变化必须换 ID

    def test_locator_change_changes_id(self):
        a = entry_id("rpgmv", "loc1", "text")
        b = entry_id("rpgmv", "loc2", "text")
        assert a != b  # 同文本不同位置 -> 不同条目

    def test_engine_change_changes_id(self):
        a = entry_id("rpgmv", "loc1", "text")
        b = entry_id("renpy", "loc1", "text")
        assert a != b  # 不同引擎绝不串 ID

    def test_id_is_16_hex_chars(self):
        eid = entry_id("rpgmv", "loc", "text")
        assert len(eid) == 16
        int(eid, 16)  # 必须是合法 hex

    def test_source_longer_than_256_tail_change_changes_id(self):
        """完整 source 哈希：超长文本任何位置变化 ID 都变（防 256 截断碰撞）。"""
        long_a = "あ" * 300 + "UNIQUE_TAIL_1"
        long_b = "あ" * 300 + "UNIQUE_TAIL_2"
        assert entry_id("rpgmv", "loc", long_a) != entry_id("rpgmv", "loc", long_b)

    def test_no_collision_for_shared_256_prefix(self):
        """反碰撞回归：共享前 256 字符、仅尾部不同的长文本必须得到不同 ID。"""
        base = "あ" * 256
        a = base + "TAIL1"
        b = base + "TAIL2"
        id_a = entry_id("rpgmv", "loc:1", a)
        id_b = entry_id("rpgmv", "loc:1", b)
        assert id_a != id_b

    def test_source_change_inside_256_changes_id(self):
        a = "あ" * 100 + "X" + "あ" * 155  # X 在 256 内
        b = "あ" * 100 + "Y" + "あ" * 155
        assert entry_id("rpgmv", "loc", a) != entry_id("rpgmv", "loc", b)


class TestLocatorContract:
    """ADR-0003：locator 对核心不透明；标准化只做不改变语义的整理。"""

    def test_normalize_strips_whitespace(self):
        assert normalize_locator("  $.a[0].b  ") == "$.a[0].b"

    def test_normalize_keeps_inner_content(self):
        # 含空格的 locator 标准化后内容不变（不做任何解析）
        assert normalize_locator("Map001.json: line 3") == "Map001.json: line 3"

    def test_entry_id_uses_normalized_locator(self):
        assert entry_id("rpgmv", " loc ", "t") == entry_id("rpgmv", "loc", "t")
