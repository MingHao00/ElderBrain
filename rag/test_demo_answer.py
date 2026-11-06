import unittest

from demo_answer import MAX_BODY_BYTES, present_answer, read_question

VERSION = "按中文 SRD 5.1（2014 规则，机器翻译）作答。"


class ReadQuestionTests(unittest.TestCase):
    def test_strips_a_normal_question(self) -> None:
        raw = '{"question": "  火球术射程  "}'
        self.assertEqual(read_question(raw.encode("utf-8")), "火球术射程")

    def test_rejects_empty_and_oversized_bodies(self) -> None:
        with self.assertRaises(ValueError):
            read_question(b'{"question": "  "}')
        with self.assertRaises(ValueError):
            read_question(b"x" * (MAX_BODY_BYTES + 1))
        with self.assertRaises(ValueError):
            read_question(b"not-json")


class PresentAnswerTests(unittest.TestCase):
    def test_splits_cited_sentences(self) -> None:
        raw = (
            VERSION
            + '\n\n{"type":"answer","refusal_category":null,"sentences":['
            + '{"text":"射程 150 尺。","entry_id":"srd51:spell:fireball"}]}'
        )
        shown = present_answer(raw)
        self.assertIn(VERSION, shown)
        self.assertIn("射程 150 尺。", shown)
        self.assertIn("srd51:spell:fireball", shown)
        self.assertNotIn("{", shown)

    def test_keeps_a_plain_refusal(self) -> None:
        raw = VERSION + "当前只收录中文 SRD 5.1。"
        self.assertEqual(present_answer(raw), raw)

    def test_names_an_empty_refusal(self) -> None:
        raw = (
            VERSION
            + '\n\n{"type":"refusal","refusal_category":"R2","sentences":[]}'
        )
        shown = present_answer(raw)
        self.assertIn("原文不够", shown)
        self.assertNotIn("R2", shown)


if __name__ == "__main__":
    unittest.main()
