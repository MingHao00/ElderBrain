import unittest

from pdf_items import item_entries

_ITEMS = """
## Armor
The armor table is not an item.
## Bag of Holding
## Wondrous item, uncommon
The bag holds 500 pounds.
## Bag of Tricks
## Wondrous Item, Uncommon
Pull a fuzzy object.
## Gray Bag of Tricks
| d8 | Creature |
## Armor, +1, +2, or +3
Armor (light, medium, or heavy), rare (+1), very rare (+2), or legendary (+3)
You have a bonus to AC.
"""

_FIELDS = {
    "version_token": "srd51",
    "rule_version": "5.1",
    "source_id": "src-srd51-en",
    "source_pin": "pin",
    "pdf_name": "SRD_CC_v5.1.pdf",
}


class ItemEntryTests(unittest.TestCase):
    def test_splits_on_rarity_line_and_keeps_inner_headings(self) -> None:
        entries = item_entries(_ITEMS, **_FIELDS)
        self.assertEqual([item.entry_id for item in entries], [
            "srd51:equipment:bag-of-holding",
            "srd51:equipment:bag-of-tricks",
            "srd51:equipment:armor-1-2-or-3",
        ])
        holding = entries[0]
        self.assertIn("500 pounds", holding.text)
        self.assertNotIn("fuzzy", holding.text)
        tricks = entries[1]
        self.assertIn("Gray Bag of Tricks", tricks.text)
        self.assertIn("fuzzy", tricks.text)
        armor = entries[2]
        self.assertIn("bonus to AC", armor.text)
        self.assertNotIn("500 pounds", armor.text)

    def test_stops_before_the_chapter_after_the_last_item(self) -> None:
        entries = item_entries(_AFTER_ITEMS, **_FIELDS)
        self.assertEqual([item.entry_id for item in entries], [
            "srd51:equipment:wings-of-flying",
            "srd51:equipment:orb-of-dragonkind",
        ])
        wings = entries[0]
        self.assertIn("cloak becomes wings", wings.text)
        self.assertNotIn("aware", wings.text)
        orb = entries[1]
        self.assertIn("Call dragons", orb.text)
        self.assertNotIn("stat block", orb.text)


_AFTER_ITEMS = """
## Wings of Flying
## Wondrous item, rare
The cloak becomes wings.
## Sentient Magic Items
Some items are aware.
## Orb of Dragonkind
Wondrous item, artifact
Call dragons.
## Monsters
A monster's stat block.
"""


if __name__ == "__main__":
    unittest.main()
