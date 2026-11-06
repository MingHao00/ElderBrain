import unittest

from pdf_table_repair import repair_glued_tables

_PLAIN = """
Earlier table
01–05 Something else entirely.
d100 Effect
01–05 You cast slow.
06–10 You cast faerie fire.
11–15 You are stunned until the start of your next turn.
16–20 You cast gust of wind.
34–36 An animal appears. Roll a d100 to see which.
37–46 You cast lightning bolt.
Potions of Healing
Greater healing is uncommon.
"""

_BROKEN = """
| 01-0506-1011-15 | Youcastslow. Youcastfaeriefire. Youarestunneduntilthestartofyournextturn. |
| Greaterhealing | Uncommon |
| 34-36 | Ananimalappears.Rollad100to see which. |
Roll a d100 to see which. Next sentence stays.
"""


class TableRepairTests(unittest.TestCase):
    def test_splits_merged_ranges_and_restores_spaces(self) -> None:
        text = repair_glued_tables(_BROKEN, _PLAIN)
        self.assertIn("| 01-05 | You cast slow. |", text)
        self.assertIn("| 06-10 | You cast faerie fire. |", text)
        self.assertIn("| 11-15 | You are stunned until the start of your next turn. |", text)
        self.assertNotIn("Youcastslow", text)
        self.assertIn("| Greater healing | Uncommon |", text)
        self.assertIn("| 34-36 | An animal appears. Roll a d100 to see which. |", text)
        self.assertNotIn("Ananimal", text)
        self.assertIn("Next sentence stays.", text)
        self.assertEqual(text.count("Roll a d100 to see which."), 1)
        self.assertNotIn("Something else", text)


if __name__ == "__main__":
    unittest.main()
