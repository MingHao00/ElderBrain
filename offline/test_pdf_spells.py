import unittest
from pathlib import Path

from ids import RULE_VERSION_51, RULE_VERSION_52, VERSION_TOKEN_51, VERSION_TOKEN_52
from pdf_spells import (
    SOURCE_ID_51_EN,
    SOURCE_ID_521_EN,
    SOURCE_PIN_51_EN,
    SOURCE_PIN_521_EN,
    spell_entries,
)
from pdf_text import normalize_pdf_text

_FIXTURE = """
**Find Traps** 2nd-level divination

**Casting Time:** 1 action **Range:** 120 feet

You sense the presence of any trap.

**Fireball** 3rd-level evocation

**Casting Time:** 1 action **Range:** 150 feet

Each creature in a 20-foot-radius sphere takes 8d6 fire damage.

**At Higher Levels.** The damage increases by 1d6.

**Alarm**
1st-level abjuration (ritual)

**Casting Time:** 1 minute

You set an alarm.

## Comprehend Languages 1st-level divination (ritual)

You understand the literal meaning of spoken language.

## Counterspell
3rd-level abjuration

You attempt to interrupt a creature.

**Lightning Bolt** 3rd-level evocation **Casting Time:** 1 action

A stroke of lightning.

**Fire Bolt** Evocation cantrip

**Casting Time:** 1 action

You hurl a mote of fire. The target takes 1d10 fire damage.
"""


_SRD52 = """
## Fireball
Level 3 Evocation (Sorcerer, Wizard)
**Casting Time:** Action
Each creature takes 8d6 Fire damage.

## Fire Bolt
Evocation Cantrip (Sorcerer, Wizard) **Casting Time:** Action
The target takes 1d10 Fire damage.

**Alarm** Level 1 Abjuration (Ranger, Wizard) **Casting Time:** 1 minute or Ritual
You set an alarm.
"""


class PdfSpellTests(unittest.TestCase):
    def test_splits_on_spell_headers_only(self) -> None:
        found = spell_entries(
            _FIXTURE,
            version_token=VERSION_TOKEN_51,
            rule_version=RULE_VERSION_51,
            source_id=SOURCE_ID_51_EN,
            source_pin=SOURCE_PIN_51_EN,
            pdf_name="SRD_CC_v5.1.pdf",
        )
        self.assertEqual(
            [item.title_en for item in found],
            [
                "Find Traps",
                "Fireball",
                "Alarm",
                "Comprehend Languages",
                "Counterspell",
                "Lightning Bolt",
                "Fire Bolt",
            ],
        )
        self.assertIn("1st-level abjuration (ritual)", found[2].text)
        self.assertIn("spoken language", found[3].text)
        self.assertIn("interrupt a creature", found[4].text)
        self.assertNotIn("Lightning Bolt", found[4].text)
        self.assertIn("**Casting Time:** 1 action", found[5].text)
        fireball = found[1]
        self.assertEqual(fireball.entry_id, "srd51:spell:fireball")
        self.assertEqual(fireball.concept_key, "spell:fireball")
        self.assertIn("8d6 fire damage", fireball.text)
        self.assertIn("At Higher Levels.", fireball.text)
        self.assertNotIn("Fire Bolt", fireball.text)
        self.assertEqual(fireball.lang, "en")

    def test_splits_srd52_level_lines(self) -> None:
        found = spell_entries(
            _SRD52,
            version_token=VERSION_TOKEN_52,
            rule_version=RULE_VERSION_52,
            source_id=SOURCE_ID_521_EN,
            source_pin=SOURCE_PIN_521_EN,
            pdf_name="SRD_CC_v5.2.1.pdf",
        )
        self.assertEqual(
            [item.title_en for item in found],
            ["Fireball", "Fire Bolt", "Alarm"],
        )
        self.assertEqual(found[0].entry_id, "srd52:spell:fireball")
        self.assertIn("8d6 Fire damage", found[0].text)
        self.assertIn("Level 3 Evocation", found[0].text)
        self.assertNotIn("Fire Bolt", found[0].text)

    def test_skips_class_features_and_marks_garbled_titles(self) -> None:
        text = """
## Level 3: Potent Cantrip
Your damaging cantrips affect even creatures that avoid the brunt.

## F ear
Level 3 Illusion (Bard, Sorcerer, Warlock, Wizard)
Each creature in a 30-foot Cone.

## Fireball
Level 3 Evocation (Sorcerer, Wizard)
8d6 Fire damage.
"""
        found = spell_entries(
            text,
            version_token=VERSION_TOKEN_52,
            rule_version=RULE_VERSION_52,
            source_id=SOURCE_ID_521_EN,
            source_pin=SOURCE_PIN_521_EN,
            pdf_name="SRD_CC_v5.2.1.pdf",
        )
        self.assertEqual([item.title_en for item in found], ["Fear", "Fireball"])
        self.assertEqual(found[0].entry_id, "srd52:spell:fear")
        self.assertEqual(found[0].align_status, "repaired_heading")
        self.assertEqual(found[1].align_status, "named_from_heading")

    def test_saved_page_contains_fireball(self) -> None:
        path = Path(__file__).resolve().parents[1] / "parse" / "_txt-p144.md"
        if not path.exists():
            self.skipTest("sample page not exported")
        text = normalize_pdf_text(path.read_text(encoding="utf-8"))
        found = spell_entries(
            text,
            version_token=VERSION_TOKEN_51,
            rule_version=RULE_VERSION_51,
            source_id=SOURCE_ID_51_EN,
            source_pin=SOURCE_PIN_51_EN,
            pdf_name="SRD_CC_v5.1.pdf",
        )
        names = [item.title_en for item in found]
        self.assertIn("Fireball", names)
        fireball = next(item for item in found if item.title_en == "Fireball")
        self.assertIn("8d6 fire damage", fireball.text)
        self.assertIn("150 feet", fireball.text)
        self.assertNotIn("\t", fireball.text)


if __name__ == "__main__":
    unittest.main()
