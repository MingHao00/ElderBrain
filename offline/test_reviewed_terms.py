import unittest

from html_entries import make_catalog_entry

_FIELDS = {
    "version_token": "srd52",
    "rule_version": "5.2",
    "source_id": "src-srd52-zh",
    "source_pin": "pin",
    "lang": "zh",
    "title_zh": "条目",
    "location": "file",
    "text": "正文",
    "align_status": "named_from_heading",
}


def _entry(entry_type: str, english_name: str) -> str:
    made = make_catalog_entry(
        entry_type=entry_type,
        english_name=english_name,
        **_FIELDS,
    )
    return f"{made.entry_id}|{made.align_status}"


class ReviewedHeadingTests(unittest.TestCase):
    def test_same_entry_uses_the_english_pdf_heading(self) -> None:
        self.assertEqual(
            _entry("rule", "Curse Items"),
            "srd52:rule:cursed-items|named_from_review",
        )
        self.assertEqual(
            _entry("rule", "Ranged Attack"),
            "srd52:rule:ranged-attacks|named_from_review",
        )
        self.assertEqual(
            _entry("rule", "Step5:Fill In Details"),
            "srd52:rule:step-5-character-creation-details|named_from_review",
        )
        self.assertEqual(
            _entry("rule", "Curse"),
            "srd52:rule:curses-and-magical-contagions|named_from_review",
        )
        self.assertEqual(
            _entry("rule", "Abilities"),
            "srd52:rule:using-ability-scores|named_from_review",
        )
        self.assertEqual(
            _entry("glossary", "Objects"),
            "srd52:glossary:object|named_from_review",
        )

    def test_different_scope_keeps_the_original_name(self) -> None:
        self.assertEqual(
            _entry("rule", "GM’s Toolbox"),
            "srd52:rule:gms-toolbox|named_from_heading",
        )
        self.assertEqual(
            _entry("rule", "Objects"),
            "srd52:rule:objects|named_from_heading",
        )


if __name__ == "__main__":
    unittest.main()
