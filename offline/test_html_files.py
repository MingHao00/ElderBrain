import unittest

from html_files import classify_html, entries_from_classified_html

_CLASS = "<h1>战士 Fighter</h1><p>生命骰：每战士等级 1d10。</p>"
_NO_HEADING = """
<style>html,body { font-family: Arial; }</style>
<b>陷阱 Traps</b><p>陷阱应当少量使用，否则就会失去其魅力。</p>
<b>陷坑 Pit</b><p>掉进去会受伤。</p>
"""
_SPELL_LIST = "<h2>战士法术列表 Fighter Spell List</h2><p>火球术</p>"


class ClassifyHtmlTests(unittest.TestCase):
    def test_known_paths(self) -> None:
        cases = {
            "分隔符.htm": "skip",
            "玩家手册2024/第一章：进行游戏.htm": "skip",
            "玩家手册2024/术语汇编/术语速查.htm": "skip",
            "玩家手册2024/角色职业/战士/战士法术列表.htm": "skip",
            "玩家手册2024/法术/法术详述.htm": "skip",
            "玩家手册2024/角色职业/战士/战士.htm": "single-class",
            "玩家手册2024/角色职业/战士/勇士.htm": "single-subclass",
            "玩家手册2024/角色职业/术士/超魔法选项.htm": "maroon-rule",
            "玩家手册2024/专长/通用专长.htm": "maroon-feat",
            "城主指南2024/7.宝藏/魔法物品详述/戒指/非普通.htm": "maroon-item",
            "玩家手册2024/术语汇编/状态.htm": "maroon-condition",
            "玩家手册2024/术语汇编/其他术语.htm": "maroon-glossary",
            "玩家手册2024/角色起源/种族/人类.htm": "single-species",
            "玩家手册2024/角色起源/背景/士兵.htm": "single-background",
            "玩家手册2024/进行游戏/战斗.htm": "single-rule",
            "玩家手册2024/装备/武器.htm": "single-equipment",
            "城主指南2024/3.地下城主工具箱/陷阱.htm": "single-rule",
        }
        for path, kind in cases.items():
            with self.subTest(path=path):
                self.assertEqual(classify_html(path), kind)

    def test_class_file_is_one_entry_and_spell_list_is_skipped(self) -> None:
        entries, unresolved = entries_from_classified_html(
            _CLASS,
            "玩家手册2024/角色职业/战士/战士.htm",
        )
        self.assertEqual(unresolved, [])
        self.assertEqual(entries[0].entry_id, "srd52:class:fighter")
        self.assertIn("1d10", entries[0].text)
        skipped, missing = entries_from_classified_html(
            _SPELL_LIST,
            "玩家手册2024/角色职业/战士/战士法术列表.htm",
        )
        self.assertEqual(skipped, [])
        self.assertEqual(missing, [])

    def test_file_without_heading_uses_the_first_bilingual_line(self) -> None:
        entries, unresolved = entries_from_classified_html(
            _NO_HEADING,
            "城主指南2024/3.地下城主工具箱/陷阱.htm",
        )
        self.assertEqual(unresolved, [])
        self.assertEqual(entries[0].entry_id, "srd52:rule:traps")
        self.assertIn("失去其魅力", entries[0].text)
        self.assertNotIn("font-family", entries[0].text)
        self.assertIn("掉进去会受伤", entries[0].text)


if __name__ == "__main__":
    unittest.main()
