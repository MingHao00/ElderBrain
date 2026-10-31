import unittest
from pathlib import Path

from pdf_text import normalize_pdf_text

_SAMPLE = (
    "**Fireball** 3rd-\u00ad\u2010\u2011level\tevocation\n"
    "**Casting\t \u00a0Time:** 1\t \u00a0action **Range:**\t \u00a0150\t \u00a0feet\n"
    "a\t \u00a020-\u00ad\u2010\u2011foot-\u00ad\u2010\u2011radius sphere takes\t \u00a08d6\t \u00a0fire\n"
)


class PdfTextTests(unittest.TestCase):
    def test_restores_spaces_hyphens_and_damage(self) -> None:
        text = normalize_pdf_text(_SAMPLE)
        self.assertIn("**Fireball** 3rd-level evocation", text)
        self.assertIn("**Casting Time:** 1 action **Range:** 150 feet", text)
        self.assertIn("20-foot-radius", text)
        self.assertIn("8d6 fire", text)
        self.assertNotIn("\t", text)
        self.assertNotIn("\u00a0", text)

    def test_saved_page_when_present(self) -> None:
        path = Path(__file__).resolve().parents[1] / "parse" / "_txt-p144.md"
        if not path.exists():
            self.skipTest("sample page not exported")
        text = normalize_pdf_text(path.read_text(encoding="utf-8"))
        self.assertIn("**Fireball** 3rd-level evocation", text)
        self.assertIn("8d6 fire damage", text)
        self.assertIn("150 feet", text)
        self.assertIn("20-foot-radius", text)


if __name__ == "__main__":
    unittest.main()
