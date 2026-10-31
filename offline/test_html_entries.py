import unittest

from html_entries import entries_from_monster_html, entries_from_spell_html

_SPELLS = """
<H4 id="Acid_Splash">酸液飞溅｜Acid Splash</H4>
<P><STRONG>施法时间：</STRONG>动作<BR><STRONG>施法距离：</STRONG>60 尺<BR>受到1d6点强酸伤害。</P>
<H4 id="Fireball">火球术｜Fireball</H4>
<P><STRONG>施法时间：</STRONG>动作<BR>失败受到8d6点火焰伤害。</P>
"""

_MONSTER = """
<h5>底栖魔鱼Aboleth</h5>
<div class="sub-line">大型异怪，守序邪恶</div>
<table><tr><td><strong>AC </strong>17</td><td><strong>HP </strong>150（20d10+40）</td></tr></table>
"""


class SpellHtmlTests(unittest.TestCase):
    def test_splits_on_h4_and_keeps_header(self) -> None:
        entries, unresolved = entries_from_spell_html(_SPELLS, "法术详述/0环.htm")
        self.assertEqual(unresolved, [])
        self.assertEqual([item.entry_id for item in entries], [
            "srd52:spell:acid-splash",
            "srd52:spell:fireball",
        ])
        acid = entries[0]
        self.assertEqual(acid.title_zh, "酸液飞溅")
        self.assertIn("施法时间：动作", acid.text)
        self.assertIn("1d6", acid.text)
        self.assertNotIn("8d6", acid.text)
        self.assertIn("8d6", entries[1].text)
        self.assertEqual(acid.location, "法术详述/0环.htm#Acid_Splash")


class MonsterHtmlTests(unittest.TestCase):
    def test_keeps_stat_block_together(self) -> None:
        entries, unresolved = entries_from_monster_html(_MONSTER, "异怪/底栖魔鱼.htm")
        self.assertEqual(unresolved, [])
        self.assertEqual(len(entries), 1)
        monster = entries[0]
        self.assertEqual(monster.entry_id, "srd52:monster:aboleth")
        self.assertEqual(monster.title_zh, "底栖魔鱼")
        self.assertIn("AC 17", monster.text)
        self.assertIn("20d10+40", monster.text)

    def test_confirmed_titles_use_the_english_pdf_name(self) -> None:
        entries, unresolved = entries_from_monster_html(_RENAMED, "怪兽/枭熊.htm")
        self.assertEqual([item.entry_id for item in entries], [
            "srd52:monster:owlbear",
            "srd52:monster:swarm-of-crawling-claws",
        ])
        self.assertEqual(entries[0].align_status, "named_from_review")
        self.assertEqual(entries[0].title_zh, "枭熊")
        self.assertEqual(unresolved, ["怪兽/枭熊.htm#Giant_Squid#no-english:Giant Squid"])


_RENAMED = """
<h5 id="Owlbears">枭熊Owlbears</h5>
<p>生命值 59</p>
<h5 id="Swarm_of_Crawling_Claw">蠕行之爪集群Swarm of Crawling Claw</h5>
<p>生命值 49</p>
<h5 id="Giant_Squid">巨乌贼Giant Squid</h5>
<p>生命值 120</p>
"""


if __name__ == "__main__":
    unittest.main()
