import unittest

from monster_names import canonical_monster_name, monster_file_identity


class MonsterNameTests(unittest.TestCase):
    def test_plain_name_keeps_its_status(self) -> None:
        self.assertEqual(
            canonical_monster_name("Aboleth", "named_from_heading"),
            ("Aboleth", "named_from_heading"),
        )

    def test_file_identity_strips_only_the_category(self) -> None:
        self.assertEqual(
            monster_file_identity("Knight (NPC)"),
            ("monster", "Knight", "stripped_category"),
        )


if __name__ == "__main__":
    unittest.main()
