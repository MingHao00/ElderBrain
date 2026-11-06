import unittest
from datetime import datetime

from config import REPO_ROOT
from generation_call import deepseek_yuan, is_deepseek_peak, qwen_yuan
from generation_prompt import DELIMITER_MARK, render_prompt
from generation_questions import ROLE_INTERCEPT, ROLE_MODEL, load_generation_questions
from generation_score import parse_generation, score_generation

ACCEPTANCE = REPO_ROOT / "design" / "05-acceptance.md"


class GenerationQuestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.questions = load_generation_questions(ACCEPTANCE)
        self.by_id = {item.question_id: item for item in self.questions}

    def test_keeps_chinese_daily_and_drops_the_rest(self) -> None:
        self.assertIn("L06", self.by_id)
        self.assertIn("R01", self.by_id)
        self.assertNotIn("L01", self.by_id)
        self.assertNotIn("L09", self.by_id)
        self.assertNotIn("L16", self.by_id)
        self.assertNotIn("C01", self.by_id)
        self.assertNotIn("M02", self.by_id)

    def test_interceptor_questions_are_not_sent_to_the_model(self) -> None:
        self.assertEqual(self.by_id["R15"].role, ROLE_INTERCEPT)
        self.assertEqual(self.by_id["R18"].role, ROLE_INTERCEPT)
        self.assertEqual(self.by_id["L06"].role, ROLE_MODEL)
        self.assertEqual(self.by_id["R01"].expected_category, "R1")
        self.assertEqual(self.by_id["R01"].expected_type, "refusal")
        self.assertTrue(self.by_id["R09"].require_dm)

    def test_partial_and_clarify_questions_are_answers(self) -> None:
        for question_id in ("R04", "R05"):
            item = self.by_id[question_id]
            self.assertEqual(item.expected_type, "answer")
            self.assertIsNone(item.expected_category)
            self.assertFalse(item.require_clarify)
        clarify = self.by_id["R17"]
        self.assertEqual(clarify.expected_type, "answer")
        self.assertIsNone(clarify.expected_category)
        self.assertTrue(clarify.require_clarify)
        self.assertEqual(self.by_id["R06"].expected_category, "R3")


class GenerationScoreTests(unittest.TestCase):
    def test_dice_must_appear_in_the_cited_parent(self) -> None:
        payload = parse_generation(
            '{"type":"answer","refusal_category":null,"sentences":'
            '[{"text":"每发 1d4+1 力场。","entry_id":"srd51:spell:magic-missile"}]}'
        )
        parents = {"srd51:spell:magic-missile": "每发造成 1d4+1 力场伤害"}
        score = score_generation(
            payload, "answer", None, False, False, set(parents), parents
        )
        self.assertTrue(score.numbers_ok)
        changed = dict(payload)
        changed["sentences"] = [{"text": "每发 9d9。", "entry_id": "srd51:spell:magic-missile"}]
        missed = score_generation(changed, "answer", None, False, False, set(parents), parents)
        self.assertFalse(missed.numbers_ok)

    def test_entry_identifier_is_not_a_game_number(self) -> None:
        payload = {
            "type": "answer",
            "refusal_category": None,
            "sentences": [
                {"text": "见 srd51:rule:combat。", "entry_id": "srd51:rule:combat"}
            ],
        }
        allowed = {"srd51:rule:combat"}
        score = score_generation(
            payload, "answer", None, False, False, allowed, {"srd51:rule:combat": "近战攻击。"}
        )
        self.assertTrue(score.numbers_ok)

    def test_outside_entry_fails_the_citation_scope(self) -> None:
        payload = {
            "type": "answer",
            "refusal_category": None,
            "sentences": [{"text": "见另一条。", "entry_id": "srd51:spell:fireball"}],
        }
        score = score_generation(payload, "answer", None, False, False, set(), {})
        self.assertFalse(score.citation_scope_ok)
        self.assertTrue(score.version_ok)

    def test_wrong_version_entry_fails(self) -> None:
        payload = {
            "type": "answer",
            "refusal_category": None,
            "sentences": [{"text": "见新版。", "entry_id": "srd52:spell:fireball"}],
        }
        allowed = {"srd52:spell:fireball"}
        score = score_generation(payload, "answer", None, False, False, allowed, {})
        self.assertFalse(score.version_ok)

    def test_answer_question_refused_is_a_false_refusal(self) -> None:
        payload = {"type": "refusal", "refusal_category": "R2", "sentences": []}
        score = score_generation(payload, "answer", None, False, False, set(), {})
        self.assertTrue(score.false_refusal)
        self.assertFalse(score.category_ok)

    def test_refusal_category_must_match(self) -> None:
        payload = {
            "type": "refusal",
            "refusal_category": "R3",
            "sentences": [{"text": "没有这条。", "entry_id": None}],
        }
        score = score_generation(payload, "refusal", "R1", False, False, set(), {})
        self.assertFalse(score.category_ok)

    def test_clarify_question_is_one_uncited_question(self) -> None:
        asked = {
            "type": "answer",
            "refusal_category": None,
            "sentences": [{"text": "你指的是哪个法术？", "entry_id": None}],
        }
        score = score_generation(asked, "answer", None, False, True, set(), {})
        self.assertTrue(score.require_clarify_ok)
        self.assertTrue(score.category_ok)
        cited = {
            "type": "answer",
            "refusal_category": None,
            "sentences": [{"text": "火球术可以用。", "entry_id": "srd51:spell:fireball"}],
        }
        missed = score_generation(cited, "answer", None, False, True, set(), {})
        self.assertFalse(missed.require_clarify_ok)

    def test_prose_is_not_structured(self) -> None:
        self.assertIsNone(parse_generation("火球术造成 8d6。"))


class GenerationPromptTests(unittest.TestCase):
    def test_delimiter_is_written_into_the_prompt(self) -> None:
        delimiter = "a1b2c3d4e5f60718"
        text = render_prompt(delimiter)
        self.assertNotIn(DELIMITER_MARK, text)
        self.assertIn(delimiter, text)
        self.assertIn("sentences 必须是 []", text)
        self.assertIn("逐字复制", text)
        self.assertIn("R1 超出 SRD", text)
        self.assertIn("类别 R2", text)
        with self.assertRaises(ValueError):
            render_prompt("")
        with self.assertRaises(ValueError):
            render_prompt("has space")


class GenerationPriceTests(unittest.TestCase):
    def test_friday_afternoon_before_two_is_off_peak(self) -> None:
        self.assertFalse(is_deepseek_peak(datetime(2026, 11, 6, 13, 55)))
        self.assertTrue(is_deepseek_peak(datetime(2026, 11, 6, 14, 0)))

    def test_prices_follow_the_published_rates(self) -> None:
        self.assertAlmostEqual(deepseek_yuan(1_000_000, 0, 0, False), 1.0)
        self.assertAlmostEqual(qwen_yuan(1000, 0), 0.0002)
        self.assertAlmostEqual(qwen_yuan(1_000_000, 0), 1.2)


if __name__ == "__main__":
    unittest.main()
