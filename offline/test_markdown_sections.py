import unittest

from markdown_entries import folder_entry_type, section_kind
from markdown_sections import section_entries

_CONDITIONS = """# 状态

状态是临时的游戏状况。

## 目盲

你无法视物。

## 被擒抱

你的速度变为 0。
"""

_CLASS = """# 战士

### 职业特性

生命骰：每战士等级 1d10。

## 武学原型

### 斗士

#### 强化重击

你的武器攻击在掷出 19 或 20 时造成重击。
"""

_TERMS = {
    "condition": {"目盲": "Blinded"},
    "subclass": {"勇士": "Champion"},
}


class SectionKindTests(unittest.TestCase):
    def test_routes_whole_files_and_splits(self) -> None:
        self.assertEqual(folder_entry_type("docs/Treasure/Bag of Holding.md"), "equipment")
        self.assertEqual(folder_entry_type("docs/Gameplay/Combat.md"), "rule")
        self.assertEqual(folder_entry_type("docs/Equipment/Weapons.md"), "equipment")
        self.assertEqual(folder_entry_type("docs/Gamemastering/Conditions.md"), "")
        self.assertEqual(section_kind("docs/Gamemastering/Conditions.md"), "condition")
        self.assertEqual(section_kind("docs/Classes/Fighter.md"), "class")
        self.assertEqual(section_kind("docs/Spells/Fireball.md"), "")


class SectionEntryTests(unittest.TestCase):
    def test_conditions_use_terms_and_keep_unmatched(self) -> None:
        entries, unresolved = section_entries(
            _CONDITIONS,
            relative_path="docs/Gamemastering/Conditions.md",
            kind="condition",
            terms=_TERMS,
        )
        ids = [item.entry_id for item in entries]
        self.assertIn("srd51:rule:conditions", ids)
        self.assertIn("srd51:condition:blinded", ids)
        self.assertIn("srd51:condition:grappled", ids)
        self.assertEqual(unresolved, [])
        blinded = next(item for item in entries if item.entry_id.endswith("blinded"))
        grappled = next(item for item in entries if item.entry_id.endswith("grappled"))
        self.assertEqual(blinded.align_status, "named_from_term")
        self.assertEqual(grappled.align_status, "named_from_review")
        self.assertIn("你无法视物", blinded.text)
        self.assertNotIn("速度变为 0", blinded.text)
        self.assertIn("速度变为 0", grappled.text)

    def test_class_stops_before_subclass_even_when_subclass_name_differs(self) -> None:
        entries, unresolved = section_entries(
            _CLASS,
            relative_path="docs/Classes/Fighter.md",
            kind="class",
            terms=_TERMS,
        )
        self.assertEqual([item.entry_id for item in entries], [
            "srd51:class:fighter",
            "srd51:subclass:champion",
        ])
        self.assertIn("1d10", entries[0].text)
        self.assertNotIn("强化重击", entries[0].text)
        self.assertIn("强化重击", entries[1].text)
        self.assertEqual(entries[1].align_status, "named_from_review")
        self.assertEqual(unresolved, [])


if __name__ == "__main__":
    unittest.main()
