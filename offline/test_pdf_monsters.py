import unittest

from pdf_monsters import monster_entries
from pdf_monsters51 import monster_entries as monster_entries_51

_MONSTERS = """
## Before
Large room, not a monster.
## Monsters A–Z
## Aboleth
## Aboleth
Large Aberration, Lawful Evil
**AC** 17
**HP** 150 (20d10 + 40)
## Traits
**Amphibious.** Breathes air and water.
## Actions
**Tentacle.** Hit: 12 (2d6 + 5)
## Adult Red Dragon
Huge Dragon, Chaotic Evil
**AC** 19
# Animals
The following are beasts.
"""

_FIELDS = {
    "version_token": "srd52",
    "rule_version": "5.2",
    "source_id": "src-srd521-en",
    "source_pin": "pin",
    "pdf_name": "SRD_CC_v5.2.1.pdf",
}


class MonsterEntryTests(unittest.TestCase):
    def test_keeps_stat_block_and_stops_before_the_next_monster(self) -> None:
        entries = monster_entries(_MONSTERS, **_FIELDS)
        self.assertEqual([item.entry_id for item in entries], [
            "srd52:monster:aboleth",
            "srd52:monster:adult-red-dragon",
        ])
        aboleth = entries[0]
        self.assertIn("20d10 + 40", aboleth.text)
        self.assertIn("Amphibious", aboleth.text)
        self.assertIn("2d6 + 5", aboleth.text)
        self.assertNotIn("**AC** 19", aboleth.text)
        self.assertIn("**AC** 19", entries[1].text)
        self.assertNotIn("beasts", entries[1].text)

    def test_size_line_inside_a_one_cell_table_still_starts_a_monster(self) -> None:
        entries = monster_entries(_TABLE_MONSTERS, **_FIELDS)
        self.assertEqual([item.entry_id for item in entries], [
            "srd52:monster:oni",
            "srd52:monster:hobgoblin-captain",
            "srd52:monster:pegasus",
            "srd52:monster:wolf",
        ])
        oni = entries[0]
        self.assertIn("**AC** 17", oni.text)
        self.assertNotIn("**AC** 13", oni.text)
        self.assertIn("**AC** 13", entries[3].text)

    def test_plain_name_and_html_size_cell_start_a_monster(self) -> None:
        entries = monster_entries(_LOOSE_MONSTERS, **_FIELDS)
        self.assertEqual([item.entry_id for item in entries], [
            "srd52:monster:awakened-tree",
            "srd52:monster:axe-beak",
            "srd52:monster:shield-guardian",
        ])
        tree = entries[0]
        self.assertIn("**AC** 13", tree.text)
        self.assertNotIn("1d8 + 2", tree.text)
        axe_beak = entries[1]
        self.assertIn("1d8 + 2", axe_beak.text)
        self.assertIn("**CR** 7", entries[2].text)


_LOOSE_MONSTERS = """
## Monsters A–Z
## Awakened Tree
Huge Plant, Neutral
**AC** 13
## Actions
**Slam.** Hit: 14.
Axe Beak
Axe Beak
Large Monstrosity, Unaligned
**AC** 11
## Senses Passive Perception 10
**CR** 1/4
## Actions
**Beak.** Hit: 6 (1d8 + 2) Slashing damage.
## Shield Guardian
## Shield Guardian
<table><tr><td colspan="2">Large Construct, Unaligned</td></tr></table>
**CR** 7
# Animals
The following are beasts.
"""


_TABLE_MONSTERS = """
## Monsters A–Z
## Oni
| Oni |
| --- |
Large Fiend, Lawful Evil
**AC** 17
## Hobgoblin Captain
| Medium Fey (Goblinoid), Lawful Evil |
**AC** 17
## Pegasus
Pegasus
Large Celestial, Chaotic Good
**AC** 12
| Wolf |
| --- |
Medium Beast, Unaligned
**AC** 13
| 4 | Axe Beak | 8 | Tiger |
"""


class LegacyMonsterEntryTests(unittest.TestCase):
    def test_bold_name_and_same_line_size_both_start_a_monster(self) -> None:
        entries = monster_entries_51(_LEGACY_MONSTERS, **_FIELDS_51)
        self.assertEqual([item.entry_id for item in entries], [
            "srd51:monster:aboleth",
            "srd51:monster:deva",
            "srd51:monster:ancient-red-dragon",
            "srd51:monster:adult-red-dragon",
            "srd51:monster:adult-black-dragon",
            "srd51:monster:ape",
            "srd51:monster:saber-toothed-tiger",
        ])
        aboleth = entries[0]
        self.assertIn("18d10 + 36", aboleth.text)
        self.assertIn("2d6 + 5", aboleth.text)
        self.assertNotIn("celestial", aboleth.text)
        ancient = entries[2]
        self.assertIn("28d20 + 252", ancient.text)
        self.assertNotIn("19d12 + 133", ancient.text)
        self.assertNotIn("can't see", entries[3].text)
        self.assertIn("17d12 + 85", entries[4].text)
        self.assertNotIn("can't see", entries[4].text)
        self.assertIn("3d8 + 6", entries[5].text)
        self.assertNotIn("easy ways", entries[5].text)
        self.assertIn("7d10 + 14", entries[6].text)


_FIELDS_51 = {
    "version_token": "srd51",
    "rule_version": "5.1",
    "source_id": "src-srd51-en",
    "source_pin": "pin",
    "pdf_name": "SRD_CC_v5.1.pdf",
}

_LEGACY_MONSTERS = """
## Monsters (A)
**Aboleth**
Large aberration, lawful evil
**Armor Class** 17
**Hit Points** 135 (18d10 + 36)
## Actions
**Tentacle.** Hit: 12 (2d6 + 5)
## Angels
**Deva**
Medium celestial, lawful good
**Armor Class** 17
**Red Dragon**
**Ancient Red Dragon** Gargantuan dragon, chaotic evil **Hit Points** 546 (28d20 + 252)
## Actions
**Bite.** Hit: 21 (2d10 + 10)
**Adult Red Dragon**
Huge dragon, chaotic evil
**Hit Points** 256 (19d12 + 133)
## Adult Black Dragon
Huge dragon, chaotic evil
**Hit Points** 195 (17d12 + 85)
# Appendix PH-A: Conditions
## Blinded
A blinded creature can't see.
# Appendix MM-A: Miscellaneous Creatures
## Ape
Medium beast, unaligned
**Hit Points** 19 (3d8 + 6)
## Customizing NPCs
There are many easy ways.
**Saber-****-Toothed Tiger**
Large beast, unaligned
**Hit Points** 52 (7d10 + 14)
"""


if __name__ == "__main__":
    unittest.main()
