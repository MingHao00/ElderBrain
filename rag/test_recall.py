import unittest

from config import REPO_ROOT
from recall_questions import load_recall_questions
from recall_score import is_hit, rate

ACCEPTANCE = REPO_ROOT / "design" / "05-acceptance.md"
CATALOG = REPO_ROOT / "parse" / "catalog" / "src-srd51-zh.jsonl"


class RecallQuestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.questions = load_recall_questions(ACCEPTANCE, CATALOG)
        self.by_id = {item.question_id: item for item in self.questions}

    def test_keeps_chinese_51_and_drops_excluded(self) -> None:
        ids = set(self.by_id)
        self.assertIn("L06", ids)
        self.assertIn("M01", ids)
        self.assertIn("R04", ids)
        self.assertNotIn("M02", ids)
        self.assertNotIn("L01", ids)
        self.assertNotIn("L16", ids)
        self.assertNotIn("C03", ids)
        self.assertNotIn("V05", ids)
        self.assertNotIn("R01", ids)

    def test_known_entries(self) -> None:
        self.assertEqual(self.by_id["L06"].entry_ids, ("srd51:spell:magic-missile",))
        self.assertEqual(self.by_id["L07"].entry_ids, ("srd51:subclass:life-domain",))
        self.assertEqual(self.by_id["R05"].entry_ids, ("srd51:subclass:evoker",))
        self.assertEqual(self.by_id["M01"].entry_ids, ("srd51:rule:combat",))
        self.assertEqual(self.by_id["L09"].split, "放行")


class RecallScoreTests(unittest.TestCase):
    def test_every_gold_entry_must_be_inside_the_window(self) -> None:
        gold = ("srd51:spell:magic-missile",)
        ranked = ["srd51:spell:fireball", "srd51:spell:magic-missile"]
        self.assertFalse(is_hit(gold, ranked, 1))
        self.assertTrue(is_hit(gold, ranked, 5))

    def test_rate_uses_the_question_count(self) -> None:
        self.assertEqual(rate([], []), 0.0)
        questions = load_recall_questions(ACCEPTANCE, CATALOG)[:2]
        self.assertEqual(rate(questions, [True, False]), 0.5)


if __name__ == "__main__":
    unittest.main()
