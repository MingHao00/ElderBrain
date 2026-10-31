import unittest

from html_entries import CatalogEntry
from ids import RULE_VERSION_51, VERSION_TOKEN_51
from pdf_pages import locate_entries, page_texts
from pdf_spells import spell_entries

_STRUCTURED = {
    "pages": [
        {"page_idx": 0, "blocks": [{
            "type": "text",
            "content": "Delayed Blast Fireball 7th-level evocation",
        }]},
        {
            "page_idx": 1,
            "blocks": [{
                "type": "text",
                "content": "**Fireball**\t \u00a03rd-\u00ad\u2010\u2011level\t \u00a0evocation\n8d6 fire",
            }],
        },
    ]
}


class PdfPageTests(unittest.TestCase):
    def test_uses_the_spell_page_not_a_longer_name(self) -> None:
        pages = page_texts(_STRUCTURED)
        found = spell_entries(
            "**Fireball** 3rd-level evocation\n8d6 fire",
            version_token=VERSION_TOKEN_51,
            rule_version=RULE_VERSION_51,
            source_id="src-srd51-en",
            source_pin="pin",
            pdf_name="SRD_CC_v5.1.pdf",
        )
        located = locate_entries(found, pages, "SRD_CC_v5.1.pdf")
        self.assertEqual(located[0].location, "SRD_CC_v5.1.pdf#p2")
        self.assertIsInstance(located[0], CatalogEntry)


if __name__ == "__main__":
    unittest.main()
