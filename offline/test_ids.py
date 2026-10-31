import unittest

from ids import concept_key, entry_id, slug


class SlugTests(unittest.TestCase):
    def test_single_word(self) -> None:
        self.assertEqual(slug("Fireball"), "fireball")

    def test_spaces_become_hyphens(self) -> None:
        self.assertEqual(slug("Acid Splash"), "acid-splash")

    def test_parentheses_and_apostrophes(self) -> None:
        self.assertEqual(slug("Adult Red Dragon (Chromatic)"), "adult-red-dragon-chromatic")
        self.assertEqual(slug("Mage's Hand"), "mages-hand")

    def test_entry_id_shape(self) -> None:
        self.assertEqual(entry_id("srd51", "spell", "Fireball"), "srd51:spell:fireball")
        self.assertEqual(concept_key("spell", "Fireball"), "spell:fireball")


if __name__ == "__main__":
    unittest.main()
