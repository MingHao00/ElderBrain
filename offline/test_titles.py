import unittest

from titles import split_bilingual_title


class TitleTests(unittest.TestCase):
    def test_fullwidth_pipe(self) -> None:
        zh_name, en_name = split_bilingual_title("酸液飞溅｜Acid Splash")
        self.assertEqual(zh_name, "酸液飞溅")
        self.assertEqual(en_name, "Acid Splash")

    def test_glued_monster_name(self) -> None:
        zh_name, en_name = split_bilingual_title("底栖魔鱼Aboleth")
        self.assertEqual(zh_name, "底栖魔鱼")
        self.assertEqual(en_name, "Aboleth")

    def test_keeps_every_word_of_a_glued_english_name(self) -> None:
        zh_name, en_name = split_bilingual_title("山丘巨人Hill Giant")
        self.assertEqual(zh_name, "山丘巨人")
        self.assertEqual(en_name, "Hill Giant")
        zh_name, en_name = split_bilingual_title("食人魔丧尸 Ogre Zombie")
        self.assertEqual(zh_name, "食人魔丧尸")
        self.assertEqual(en_name, "Ogre Zombie")

    def test_space_between_names(self) -> None:
        zh_name, en_name = split_bilingual_title("火童 Magmin")
        self.assertEqual(zh_name, "火童")
        self.assertEqual(en_name, "Magmin")

    def test_digits_slash_and_colon_stay_in_the_english_name(self) -> None:
        cases = {
            "D20检定 D20 Tests": ("D20检定", "D20 Tests"),
            "优势/劣势 Advantage/Disadvantage": ("优势/劣势", "Advantage/Disadvantage"),
            "第一步：选择职业 Step 1:Choose a Class": (
                "第一步：选择职业",
                "Step 1:Choose a Class",
            ),
            "附录C：术语汇编 Rules Glossary": ("附录C：术语汇编", "Rules Glossary"),
            "“次日黎明”“The Next Dawn”": ("次日黎明", "The Next Dawn"),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(split_bilingual_title(raw), expected)

    def test_chinese_only(self) -> None:
        zh_name, en_name = split_bilingual_title("目盲")
        self.assertEqual(zh_name, "目盲")
        self.assertEqual(en_name, "")


if __name__ == "__main__":
    unittest.main()
