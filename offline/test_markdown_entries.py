import unittest

from markdown_entries import entry_from_markdown_file, folder_entry_type, is_list_filename


class MarkdownEntryTests(unittest.TestCase):
    def test_spell_file_uses_filename(self) -> None:
        text = "### 火球术\n\n**射程：** 150 尺\n\n失败受到 8d6 点火焰伤害。\n"
        entry = entry_from_markdown_file(
            text,
            relative_path="docs/Spells/Fireball.md",
            entry_type="spell",
            english_name="Fireball",
        )
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.entry_id, "srd51:spell:fireball")
        self.assertEqual(entry.title_zh, "火球术")
        self.assertIn("8d6", entry.text)
        self.assertIn("150 尺", entry.text)

    def test_alt_and_lists_are_not_entries(self) -> None:
        self.assertEqual(folder_entry_type("docs/Spells (Alt)/Spells A.md"), "")
        self.assertEqual(folder_entry_type("docs/Monsters/# Monster Statistics.md"), "")
        self.assertEqual(folder_entry_type("docs/Spells/Fireball.md"), "spell")
        self.assertEqual(folder_entry_type("docs/Monsters/Aboleth.md"), "monster")
        self.assertTrue(is_list_filename("# Spellcasting.md"))

    def test_monster_filename_category_is_not_part_of_the_id(self) -> None:
        entry = _monster("Ape (Creature)", "### 猿\n\n护甲等级 12\n")
        self.assertEqual(entry.entry_id, "srd51:monster:ape")
        self.assertEqual(entry.title_en, "Ape")
        self.assertEqual(entry.title_zh, "猿")
        self.assertEqual(entry.align_status, "stripped_category")

    def test_wyrmling_word_order_follows_the_english_pdf(self) -> None:
        entry = _monster("Wyrmling Black Dragon (Chromatic)", "# 黑龙雏龙\n\n生命值 33\n")
        self.assertEqual(entry.entry_id, "srd51:monster:black-dragon-wyrmling")
        self.assertEqual(entry.title_en, "Black Dragon Wyrmling")
        self.assertEqual(entry.align_status, "named_from_review")

    def test_parenthetical_that_is_part_of_the_name_stays(self) -> None:
        entry = _monster("Gnome, Deep (Svirfneblin)", "### 深地侏儒\n\n")
        self.assertEqual(entry.title_en, "Gnome, Deep (Svirfneblin)")
        self.assertEqual(entry.align_status, "named_from_filename")

    def test_template_and_npc_notes_are_rules(self) -> None:
        template = _monster("Half-Dragon Template", "## 半龙模板\n\n保留其数据。\n")
        notes = _monster("Customizing NPCs (NPC)", "### 自定义 NPC\n\n替换法术。\n")
        self.assertEqual(template.entry_id, "srd51:rule:half-dragon-template")
        self.assertEqual(template.align_status, "named_from_filename")
        self.assertEqual(notes.entry_id, "srd51:rule:customizing-npcs")
        self.assertEqual(notes.title_en, "Customizing NPCs")
        self.assertEqual(notes.align_status, "stripped_category")

    def test_abilities_file_uses_the_english_chapter_heading(self) -> None:
        entry = entry_from_markdown_file(
            "# 属性\n\n六项属性。\n",
            relative_path="docs/Gameplay/Abilities.md",
            entry_type="rule",
            english_name="Abilities",
        )
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.entry_id, "srd51:rule:using-ability-scores")
        self.assertEqual(entry.align_status, "named_from_review")


def _monster(english_name: str, text: str):
    entry = entry_from_markdown_file(
        text,
        relative_path=f"docs/Monsters/{english_name}.md",
        entry_type="monster",
        english_name=english_name,
    )
    assert entry is not None
    return entry


if __name__ == "__main__":
    unittest.main()
