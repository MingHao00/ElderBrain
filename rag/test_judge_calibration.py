import unittest

from judge_calibration import cited_items, review_markdown
from judge_prompt import JUDGE_PROMPT, JUDGE_PROMPT_VERSION


class JudgeCalibrationTests(unittest.TestCase):
    def test_prompt_only_asks_for_support(self) -> None:
        self.assertIn("支持", JUDGE_PROMPT)
        self.assertIn("不支持", JUDGE_PROMPT)
        self.assertTrue(JUDGE_PROMPT_VERSION)

    def test_null_entry_is_not_sent_to_the_judge(self) -> None:
        raw = (
            '{"type":"answer","refusal_category":null,"sentences":['
            '{"text":"你指的是哪个法术？","entry_id":null},'
            '{"text":"每发 1d4+1。","entry_id":"srd51:spell:magic-missile"}]}'
        )
        parents = [{"entry_id": "srd51:spell:magic-missile", "text": "1d4 + 1 点力场伤害"}]
        items = cited_items(raw, parents)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["entry_id"], "srd51:spell:magic-missile")
        self.assertIn("1d4", str(items[0]["source"]))

    def test_review_shows_the_sentence_before_the_source(self) -> None:
        text = review_markdown(
            [{
                "question_id": "L06",
                "model_name": "deepseek-flash",
                "text": "造成 1d4+1。",
                "entry_id": "srd51:spell:magic-missile",
                "source": "1d4 + 1 点力场伤害",
            }],
            {"L06": "魔法飞弹每一发造成多少伤害？"},
        )
        self.assertLess(text.index("造成 1d4+1。"), text.index("1d4 + 1"))
        self.assertIn("\n评判：\n", text)
        self.assertIn("魔法飞弹每一发造成多少伤害？", text)


if __name__ == "__main__":
    unittest.main()
