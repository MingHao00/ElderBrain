import unittest

from pdf_conditions import condition_entries

_MARKED = """
## Attack Roll
An attack roll is a D20 Test.
## Blinded [Condition]
You can't see.
## Blindsight
This is not a condition.
## Charmed [Condition]
You can't attack the charmer.
"""

_APPENDIX = """
# Appendix PH-A: Conditions
Conditions alter a creature.
## Blinded
A blinded creature can't see.
## Exhaustion
Exhaustion has levels.
## Level Effect
1 Disadvantage on ability checks
## Frightened
A frightened creature has disadvantage.
# Appendix PH-B: Fantasy-Historical Pantheons
## The Celtic Pantheon
Gods of the wild.
"""

_FIELDS = {
    "version_token": "srd51",
    "rule_version": "5.1",
    "source_id": "src-srd51-en",
    "source_pin": "pin",
    "pdf_name": "SRD_CC_v5.1.pdf",
}


class ConditionEntryTests(unittest.TestCase):
    def test_marked_condition_stops_at_the_next_heading(self) -> None:
        entries = condition_entries(_MARKED, **_FIELDS)
        self.assertEqual([item.title_en for item in entries], ["Blinded", "Charmed"])
        self.assertIn("can't see", entries[0].text)
        self.assertNotIn("Blindsight", entries[0].text)
        self.assertNotIn("charmer", entries[0].text)

    def test_appendix_keeps_the_exhaustion_table_inside_exhaustion(self) -> None:
        entries = condition_entries(_APPENDIX, **_FIELDS)
        self.assertEqual(
            [item.entry_id for item in entries],
            ["srd51:condition:blinded", "srd51:condition:exhaustion", "srd51:condition:frightened"],
        )
        exhaustion = entries[1]
        self.assertIn("Level Effect", exhaustion.text)
        self.assertIn("Disadvantage on ability checks", exhaustion.text)
        self.assertNotIn("Celtic", exhaustion.text)
        self.assertNotIn("frightened", entries[0].text.lower())


if __name__ == "__main__":
    unittest.main()
