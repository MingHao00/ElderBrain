import unittest

from html_entries import CatalogEntry
from terms import term_index


def _row(entry_type: str, title_zh: str, title_en: str) -> CatalogEntry:
    return CatalogEntry(
        entry_id="srd52:x:y",
        concept_key="x:y",
        rule_version="5.2",
        source_id="src-srd52-zh",
        source_pin="pin",
        lang="zh",
        entry_type=entry_type,
        title_en=title_en,
        title_zh=title_zh,
        location="loc",
        text="body",
        align_status="named_from_heading",
    )


class TermIndexTests(unittest.TestCase):
    def test_keeps_one_english_name_and_drops_conflicts(self) -> None:
        index = term_index([
            _row("condition", "目盲", "Blinded"),
            _row("condition", "目盲", "Blinded"),
            _row("subclass", "勇士", "Champion"),
            _row("subclass", "勇士", "Hero"),
        ])
        self.assertEqual(index["condition"]["目盲"], "Blinded")
        self.assertNotIn("勇士", index.get("subclass", {}))


if __name__ == "__main__":
    unittest.main()
