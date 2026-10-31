import unittest

from html_maroon import entries_from_maroon_html

_FEATS = """
<h2>通用专长 General Feats</h2>
<p><STRONG><FONT color=#800000>擒抱者 Grappler<BR></FONT></STRONG>
你获得以下增益：优势攻击。</p>
<p><b><FONT color=#800000>警觉 Alert</FONT></b>你获得先攻熟练，并可以与盟友交换先攻。</p>
"""

_CONDITION = """
<P id="目盲"><STRONG><FONT color=#800000>目盲 Blinded【状态】<br></FONT></STRONG>
你无法视物，攻击检定具有劣势。</P>
<P id="耳聋"><FONT color=#800000><STRONG>耳聋 Defened 【状态】</STRONG></FONT>
你无法听声。</P>
"""

_ITEM = """
<P><STRONG><FONT color=#800000 size=4>跳跃戒指 Ring of
Jumping<BR></FONT></STRONG>着装此戒指期间，你可以从中施展跳跃术。
<FONT color=#800080>跳跃术Jump</FONT></P>
"""


class MaroonEntryTests(unittest.TestCase):
    def test_splits_feats_and_ignores_file_heading(self) -> None:
        entries, unresolved = entries_from_maroon_html(
            _FEATS,
            location_prefix="玩家手册2024/专长/通用专长.htm",
            entry_type="feat",
        )
        self.assertEqual(unresolved, [])
        self.assertEqual([item.entry_id for item in entries], [
            "srd52:feat:grappler",
            "srd52:feat:alert",
        ])
        self.assertIn("优势攻击", entries[0].text)
        self.assertNotIn("先攻熟练", entries[0].text)
        self.assertEqual(entries[0].align_status, "named_from_heading")

    def test_condition_mark_does_not_enter_the_slug(self) -> None:
        entries, unresolved = entries_from_maroon_html(
            _CONDITION,
            location_prefix="玩家手册2024/术语汇编/状态.htm",
            entry_type=lambda raw: "condition" if "【状态】" in raw else "glossary",
        )
        self.assertEqual(unresolved, [])
        self.assertEqual(entries[0].entry_id, "srd52:condition:blinded")
        self.assertEqual(entries[0].title_zh, "目盲")
        self.assertEqual(entries[0].location, "玩家手册2024/术语汇编/状态.htm#目盲")
        self.assertEqual(entries[1].entry_id, "srd52:condition:deafened")
        self.assertEqual(entries[1].title_en, "Deafened")
        self.assertEqual(entries[1].align_status, "repaired_heading")
        self.assertIn("你无法听声", entries[1].text)

    def test_wrapped_item_name_and_purple_spell_stay_in_body(self) -> None:
        entries, unresolved = entries_from_maroon_html(
            _ITEM,
            location_prefix="戒指/非普通.htm",
            entry_type="equipment",
        )
        self.assertEqual(unresolved, [])
        self.assertEqual(len(entries), 1)
        item = entries[0]
        self.assertEqual(item.entry_id, "srd52:equipment:ring-of-jumping")
        self.assertIn("着装此戒指期间", item.text)
        self.assertIn("跳跃术Jump", item.text)


if __name__ == "__main__":
    unittest.main()
