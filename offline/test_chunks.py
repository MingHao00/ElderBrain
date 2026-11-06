import unittest

from html_entries import CatalogEntry
from chunks import chunk_entry, chunk_list_checksum, count_cjk_words


def _entry(**overrides: str) -> CatalogEntry:
    fields: dict[str, str] = {
        "entry_id": "srd51:spell:fireball",
        "concept_key": "spell:fireball",
        "rule_version": "5.1",
        "source_id": "src-srd51-zh",
        "source_pin": "pin",
        "lang": "zh",
        "entry_type": "spell",
        "title_en": "Fireball",
        "title_zh": "火球术",
        "location": "docs/Spells/Fireball.md#火球术",
        "text": "### 火球术\n\n失败受到 8d6 点伤害。",
        "align_status": "named_from_filename",
    }
    fields.update(overrides)
    return CatalogEntry(**fields)


class ChunkEntryTests(unittest.TestCase):
    def test_spell_is_one_chunk_and_path_stays_out_of_the_body(self) -> None:
        chunks = chunk_entry(_entry(), token_limit=1000, count_tokens=len)
        self.assertEqual(len(chunks), 1)
        chunk = chunks[0]
        self.assertEqual(chunk.chunk_id, "srd51:spell:fireball#zh#0")
        self.assertEqual(chunk.section_id, "srd51:spell:fireball")
        self.assertEqual(chunk.entry_path, "Spells > Fireball")
        self.assertIn("8d6", chunk.text)
        self.assertNotIn("Spells > Fireball", chunk.text)
        self.assertTrue(chunk.embed_text.startswith("Spells > Fireball\n"))
        self.assertNotIn("https://", chunk.text)

    def test_links_keep_visible_text_only(self) -> None:
        text = "见 [火球术](https://example.com/a) 与 ![图](images/a.png)。"
        chunks = chunk_entry(_entry(text=text), token_limit=1000, count_tokens=len)
        self.assertIn("火球术", chunks[0].text)
        self.assertIn("图", chunks[0].text)
        self.assertNotIn("example.com", chunks[0].text)
        self.assertNotIn("images/", chunks[0].text)

    def test_rule_sections_follow_h2_and_drop_empty_headings(self) -> None:
        text = "# 战斗\n\n战斗开始。\n\n## 空节\n\n## 困难地形\n\n每移动 1 尺都要额外花费 1 尺。\n"
        chunks = chunk_entry(
            _entry(
                entry_id="srd51:rule:combat",
                concept_key="rule:combat",
                entry_type="rule",
                title_en="Combat",
                title_zh="战斗",
                location="docs/Gameplay/Combat.md#战斗",
                text=text,
            ),
            token_limit=1000,
            count_tokens=len,
        )
        self.assertEqual([item.section_id for item in chunks], [
            "srd51:rule:combat",
            "srd51:rule:combat#困难地形",
        ])
        self.assertNotIn("空节", chunks[1].text)
        self.assertIn("每移动 1 尺", chunks[1].text)
        self.assertEqual(chunks[1].entry_path, "Gameplay > Combat > 困难地形")

    def test_monster_keeps_one_section_even_with_h2(self) -> None:
        text = "### 猿\n\n护甲等级 12\n\n## 动作\n\n挥击。\n"
        chunks = chunk_entry(
            _entry(
                entry_id="srd51:monster:ape",
                concept_key="monster:ape",
                entry_type="monster",
                title_en="Ape",
                title_zh="猿",
                location="docs/Monsters/Ape.md#猿",
                text=text,
            ),
            token_limit=1000,
            count_tokens=len,
        )
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].section_id, "srd51:monster:ape")
        self.assertIn("挥击", chunks[0].text)
        self.assertIn("12", chunks[0].text)

    def test_h4_stays_inside_its_h3_when_the_section_is_split(self) -> None:
        text = "### 甲\n\n#### 子项\n\n" + ("甲" * 40) + "\n\n### 乙\n\n乙。\n"
        chunks = chunk_entry(_entry(text=text), token_limit=30, count_tokens=len)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(item.section_id == "srd51:spell:fireball" for item in chunks))
        holder = next(item for item in chunks if "子项" in item.text)
        self.assertNotIn("乙。", holder.text)

    def test_table_stays_intact_when_over_the_limit(self) -> None:
        text = "| 力量 | 敏捷 |\n| --- | --- |\n| 10 (+0) | 12 (+1) |\n"
        chunks = chunk_entry(_entry(text=text), token_limit=5, count_tokens=len)
        self.assertEqual(len(chunks), 1)
        self.assertIn("10 (+0)", chunks[0].text)
        self.assertIn("12 (+1)", chunks[0].text)

    def test_sentence_cut_keeps_version_dice_and_feet(self) -> None:
        text = "版本 5.1 继续。受到 8d6 伤害。距离 60 ft. 然后停。下一句结束。\n"
        chunks = chunk_entry(_entry(text=text), token_limit=16, count_tokens=len)
        bodies = [item.text for item in chunks]
        self.assertTrue(any("5.1" in body for body in bodies))
        self.assertTrue(any("8d6" in body for body in bodies))
        self.assertTrue(any("ft." in body for body in bodies))
        self.assertFalse(any(body.endswith("版本 5") or body.startswith(".1") for body in bodies))
        self.assertFalse(any("8d" in body and "8d6" not in body for body in bodies))

    def test_checksum_changes_when_the_body_changes(self) -> None:
        first = chunk_entry(_entry(), token_limit=1000, count_tokens=len)
        second = chunk_entry(_entry(text="### 火球术\n\n另一句。"), token_limit=1000, count_tokens=len)
        self.assertEqual(chunk_list_checksum(first), chunk_list_checksum(list(first)))
        self.assertNotEqual(chunk_list_checksum(first), chunk_list_checksum(second))


class TokenCountTests(unittest.TestCase):
    def test_cjk_char_and_latin_word(self) -> None:
        self.assertEqual(count_cjk_words("火球 8d6"), 3)
        self.assertEqual(count_cjk_words("5.1"), 2)


if __name__ == "__main__":
    unittest.main()
