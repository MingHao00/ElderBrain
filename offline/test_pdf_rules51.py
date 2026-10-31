import unittest

from pdf_rules51 import rule_entries

_TEXT = """
## Night Hag
**Illusory Appearance.** The hag looks different.
## Half-Dragon Template
A beast can become a half-dragon.
**Challenge.** Apply the template only when the prerequisite is met.
**Half-****-Red Dragon Veteran**
Medium humanoid (human), any alignment
**Hit Points** 65 (10d8 + 20)
## Customizing NPCs
A halfling druid might have a speed of 25 feet.
## Acolyte
Medium humanoid (any race), any alignment
**Hit Points** 9 (2d8)
"""

_FIELDS = {
    "version_token": "srd51",
    "rule_version": "5.1",
    "source_id": "src-srd51-en",
    "source_pin": "pin",
    "pdf_name": "SRD_CC_v5.1.pdf",
}


class RuleEntryTests(unittest.TestCase):
    def test_notes_stop_before_the_example_stat_block(self) -> None:
        entries = rule_entries(_TEXT, **_FIELDS)
        self.assertEqual([item.entry_id for item in entries], [
            "srd51:rule:half-dragon-template",
            "srd51:rule:customizing-npcs",
        ])
        template = entries[0]
        self.assertIn("half-dragon", template.text)
        self.assertNotIn("10d8 + 20", template.text)
        notes = entries[1]
        self.assertIn("halfling druid", notes.text)
        self.assertNotIn("2d8", notes.text)


if __name__ == "__main__":
    unittest.main()
