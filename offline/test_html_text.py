import unittest

from html_text import html_to_text


class HtmlTextTests(unittest.TestCase):
    def test_keeps_spell_header_and_damage(self) -> None:
        fragment = (
            "<P><EM>塑能 戏法</EM><BR><STRONG>施法时间：</STRONG>动作"
            "<BR><STRONG>施法距离：</STRONG>60 尺<BR>受到1d6点强酸伤害。</P>"
        )
        text = html_to_text(fragment)
        self.assertIn("施法时间：动作", text)
        self.assertIn("施法距离：60 尺", text)
        self.assertIn("1d6", text)
        self.assertNotIn("<", text)

    def test_style_and_script_are_not_body(self) -> None:
        fragment = (
            "<style>html,body { font-family: Arial; }</style>"
            "<p>陷阱 Traps</p><script>var x = 1;</script><p>应当少量使用。</p>"
        )
        text = html_to_text(fragment)
        self.assertIn("陷阱 Traps", text)
        self.assertIn("应当少量使用", text)
        self.assertNotIn("font-family", text)
        self.assertNotIn("var x", text)

    def test_table_cells_stay_separated(self) -> None:
        fragment = "<table><tr><td><strong>AC </strong>17</td><td>先攻 +7</td></tr></table>"
        text = html_to_text(fragment)
        self.assertIn("AC 17", text)
        self.assertIn("先攻 +7", text)


if __name__ == "__main__":
    unittest.main()
