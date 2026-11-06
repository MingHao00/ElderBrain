import unittest

from pdf_chapters import chapter_entries

_FIELDS_52 = {
    "version_token": "srd52",
    "rule_version": "5.2",
    "source_id": "src-srd521-en",
    "source_pin": "pin",
    "pdf_name": "SRD_CC_v5.2.1.pdf",
}
_FIELDS_51 = {
    "version_token": "srd51",
    "rule_version": "5.1",
    "source_id": "src-srd51-en",
    "source_pin": "pin",
    "pdf_name": "SRD_CC_v5.1.pdf",
}

_CLASS_TEXT = """
## Classes
## Barbarian
Core Barbarian Traits
## Level 1: Rage
You can rage.
## Barbarian Spell List
Fireball
## Barbarian Subclass: Path of the Berserker
## Level 3: Frenzy
You frenzy.
## Bard
Core Bard Traits
## Bard Subclass:
## College of Lore
Bards collect secrets.
## Druid
| Primary Ability | Wisdom |
## Ranger
<table><tr><td colspan="2">Core Ranger Traits</td></tr></table>
# Character Origins
"""

_FEAT_TEXT = """
## Feats
## Parts of a Feat
A feat has parts.
**Alert**
Origin Feat
You swap initiative.
## Grappler
General Feat (Prerequisite: Level 4+)
You can grapple.
# Equipment
"""

_SPECIES_TEXT = """
## Character Species
**Dragonborn**
**Creature Type:** Humanoid
Breath weapon.
## Dwarf
**Creature Type:** Humanoid
Stonecunning.
## Elven Lineages
| Lineage | Level |
## Feats
"""

_GLOSSARY_TEXT = """
# Rules Glossary
## Glossary Conventions
Tags in brackets.
## Ability Check
An ability check is a test.
## Attack [Action]
You attack.
## Blinded [Condition]
You can't see.
# Gameplay Toolbox
"""

_DURATION_TEXT = """
## Duration
Temporary Hit Points last until a rest.
## Spells
## Duration
Each spell has a duration.
## Spell Descriptions
## Fireball
A bright streak.
"""

_ARMOR_TEXT = """
# Rules Glossary
# Gameplay Toolbox
## Magic Items
Gleaned from hoards.
## Armor
Magical armor must be worn.
## Potions
Drink it.
## Magic Items A-Z
## Adamantine Armor
Armor, rare
"""

_OPEN_HAND = """
## Monk
## Class Features
You have ki.
## Way of the Open Hand
Monks of the open hand.
## Paladin
## Class Features
You can smite.
## Beyond 1st Level
You gain a level.
"""

_ACOLYTE = """
## Barbarian
## Class Features
Rage.
## Beyond 1st Level
You need experience.
## Backgrounds
Every story has a beginning.
## Acolyte
You served a temple.
## Suggested Characteristics
Shaped by temples.
## Equipment
Common coins come in several denominations.
"""

_ALIGNMENT = """
## Alignment
Most races have tendencies.
## Barbarian
## Class Features
Rage.
## Beyond 1st Level
You need experience.
## Alignment
Lawful good creatures keep their word.
## Languages
You speak Common.
"""


def _ids(text: str, fields: dict[str, str]) -> dict[str, str]:
    return {item.entry_id: item.text for item in chapter_entries(text, **fields)}


class ChapterEntryTests(unittest.TestCase):
    def test_class_stops_at_spell_list_and_subclass(self) -> None:
        bodies = _ids(_CLASS_TEXT, _FIELDS_52)
        barbarian = bodies["srd52:class:barbarian"]
        self.assertIn("You can rage.", barbarian)
        self.assertNotIn("Fireball", barbarian)
        self.assertNotIn("frenzy", barbarian)
        self.assertIn("You frenzy.", bodies["srd52:subclass:path-of-the-berserker"])
        self.assertIn("collect secrets", bodies["srd52:subclass:college-of-lore"])
        self.assertIn("srd52:class:druid", bodies)
        self.assertIn("srd52:class:ranger", bodies)

    def test_feats_species_and_glossary_tags(self) -> None:
        feats = _ids(_FEAT_TEXT, _FIELDS_52)
        self.assertIn("has parts", feats["srd52:feat:parts-of-a-feat"])
        self.assertIn("swap initiative", feats["srd52:feat:alert"])
        self.assertIn("can grapple", feats["srd52:feat:grappler"])
        species = _ids(_SPECIES_TEXT, _FIELDS_52)
        self.assertIn("Breath weapon.", species["srd52:species:dragonborn"])
        self.assertIn("Lineage", species["srd52:species:dwarf"])
        self.assertNotIn("srd52:species:elven-lineages", species)
        glossary = _ids(_GLOSSARY_TEXT, _FIELDS_52)
        self.assertIn("Tags in brackets.", glossary["srd52:rule:rules-glossary"])
        self.assertNotIn("You attack.", glossary["srd52:rule:rules-glossary"])
        self.assertIn("You attack.", glossary["srd52:glossary:attack"])
        self.assertNotIn("srd52:glossary:blinded", glossary)

    def test_spell_duration_and_magic_armor_are_the_later_headings(self) -> None:
        duration = _ids(_DURATION_TEXT, _FIELDS_52)
        self.assertIn("Each spell", duration["srd52:rule:duration"])
        self.assertNotIn("Temporary", duration["srd52:rule:duration"])
        armor = _ids(_ARMOR_TEXT, _FIELDS_52)
        self.assertIn("Gleaned", armor["srd52:rule:magic-items"])
        self.assertNotIn("Magical armor", armor["srd52:rule:magic-items"])
        self.assertIn("Magical armor", armor["srd52:rule:armor"])
        self.assertIn("Drink it.", armor["srd52:rule:armor"])
        self.assertNotIn("Adamantine", armor["srd52:rule:armor"])

    def test_51_subclass_alias_and_background_stop(self) -> None:
        bodies = _ids(_OPEN_HAND, _FIELDS_51)
        monk = bodies["srd51:class:monk"]
        self.assertIn("You have ki.", monk)
        self.assertNotIn("open hand", monk)
        self.assertIn("open hand", bodies["srd51:subclass:warrior-of-the-open-hand"])
        self.assertNotIn("smite", bodies["srd51:subclass:warrior-of-the-open-hand"])
        acolyte = _ids(_ACOLYTE, _FIELDS_51)
        self.assertIn("beginning", acolyte["srd51:rule:backgrounds"])
        self.assertNotIn("temple", acolyte["srd51:rule:backgrounds"])
        self.assertIn("Shaped by temples.", acolyte["srd51:background:acolyte"])
        self.assertNotIn("denominations", acolyte["srd51:background:acolyte"])
        alignment = _ids(_ALIGNMENT, _FIELDS_51)
        self.assertIn("Lawful good", alignment["srd51:rule:alignment"])
        self.assertNotIn("tendencies", alignment["srd51:rule:alignment"])
        self.assertNotIn("Common", alignment["srd51:rule:alignment"])

    def test_reviewed_spans_follow_the_chapter_boundaries(self) -> None:
        curse = _ids(_CURSE_TEXT, _FIELDS_52)
        body = curse["srd52:rule:curses-and-magical-contagions"]
        self.assertIn("magical burden", body)
        self.assertIn("one of the forms", body)
        self.assertNotIn("Extreme cold", body)
        chapters = _ids(_GAMEPLAY_51, _FIELDS_51)
        self.assertIn("Six abilities", chapters["srd51:rule:using-ability-scores"])
        self.assertNotIn("time a task", chapters["srd51:rule:using-ability-scores"])
        self.assertIn("time a task", chapters["srd51:rule:adventuring"])
        self.assertNotIn("typical combat", chapters["srd51:rule:adventuring"])
        self.assertIn("typical combat", chapters["srd51:rule:combat"])
        self.assertNotIn("Magic permeates", chapters["srd51:rule:combat"])
        appendix = _ids(_APPENDIX_51, _FIELDS_51)
        self.assertIn("alter a creature", appendix["srd51:rule:conditions"])
        self.assertNotIn("can't see", appendix["srd51:rule:conditions"])
        self.assertIn("Celtic", appendix["srd51:rule:pantheons"])
        self.assertIn("planes of existence", appendix["srd51:rule:planes"])


_CURSE_TEXT = """
# Rules Glossary
# Gameplay Toolbox
## Curses and Magical Contagions
A curse is a magical burden.
## Curses
A curse typically takes one of the forms.
## Environmental Effects
Extreme cold.
## Magic Items
Gleaned from hoards.
## Magic Items A-Z
"""

_GAMEPLAY_51 = """
## Using Ability Scores
Six abilities provide a quick description.
## Time
The GM determines the time a task requires.
## The Order of Combat
A typical combat encounter is a clash.
## Spellcasting
Magic permeates fantasy gaming worlds.
"""

_APPENDIX_51 = """
# Appendix PH-A: Conditions
Conditions alter a creature.
## Blinded
You can't see.
# Appendix PH-B: Fantasy-Historical Pantheons
The Celtic pantheon.
# Appendix PH-C: The Planes of Existence
These are the planes of existence.
# Appendix MM-A: Miscellaneous Creatures
Animals.
"""


if __name__ == "__main__":
    unittest.main()
