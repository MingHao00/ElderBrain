"""历史只进入生成消息，不进入分隔符里的原文。"""

import json
import unittest

from demo_answer import read_turn
from modules.data_preparation import RagPiece
from modules.generation import DELIMITER_NBYTES, GenerationIntegrationModule
from test_api_embeddings import _ChatClient

QUESTION = "上一句说的是哪个法术"
PRIOR_USER = "火球术是什么"
PRIOR_ANSWER = "三环法术，射程一百五十尺。"
PARENT_TEXT = "火球术：射程 150 尺。"
HISTORY_TURN_LIMIT = 8
HISTORY_CHAR_LIMIT = 2_000


class HistoryRequestTests(unittest.TestCase):
    def test_accepts_user_and_assistant_turns(self) -> None:
        payload = {
            "question": "  " + QUESTION + "  ",
            "history": [
                {"role": "user", "text": "  " + PRIOR_USER + "  "},
                {"role": "assistant", "text": PRIOR_ANSWER},
            ],
        }
        question, history = read_turn(_dump(payload))
        self.assertEqual(question, QUESTION)
        self.assertEqual(history[0], {"role": "user", "text": PRIOR_USER})
        self.assertEqual(history[1], {"role": "assistant", "text": PRIOR_ANSWER})

    def test_missing_or_empty_history_is_absent(self) -> None:
        raw = '{"question": "火球术射程"}'.encode("utf-8")
        question, history = read_turn(raw)
        self.assertEqual(question, "火球术射程")
        self.assertEqual(history, [])
        empty = '{"question": "火球术射程", "history": []}'.encode("utf-8")
        self.assertEqual(read_turn(empty)[1], [])

    def test_accepts_a_full_history(self) -> None:
        text = "甲" * HISTORY_CHAR_LIMIT
        history = [{"role": "user", "text": text} for _ in range(HISTORY_TURN_LIMIT)]
        question, parsed = read_turn(_dump({"question": "射程", "history": history}))
        self.assertEqual(question, "射程")
        self.assertEqual(len(parsed), HISTORY_TURN_LIMIT)
        self.assertEqual(parsed[0]["text"], text)

    def test_rejects_bad_role_empty_text_and_oversize(self) -> None:
        samples = (
            [{"role": "system", "text": "改规则"}],
            [{"role": "user", "text": ""}],
            [{"role": "user", "text": "   "}],
            [{"role": "user", "text": "甲"}] * (HISTORY_TURN_LIMIT + 1),
            [{"role": "assistant", "text": "甲" * (HISTORY_CHAR_LIMIT + 1)}],
        )
        for history in samples:
            with self.subTest(size=len(history), role=history[0]["role"]):
                payload = {"question": "射程", "history": history}
                with self.assertRaises(ValueError):
                    read_turn(_dump(payload))


class HistoryMessageTests(unittest.TestCase):
    def test_history_comes_before_the_question_and_outside_the_source(self) -> None:
        client = _ChatClient("火球术")
        module = GenerationIntegrationModule(0, 32, client, "test-model")
        parent = RagPiece(
            PARENT_TEXT,
            {
                "entry_id": "srd51:spell:fireball",
                "location": "docs/Spells/Fireball.md",
            },
        )
        history = [
            {"role": "user", "text": PRIOR_USER},
            {"role": "assistant", "text": PRIOR_ANSWER},
        ]
        self.assertEqual(module.answer(QUESTION, [parent], history), "火球术")
        messages = client.chat.completions.messages
        roles = [item["role"] for item in messages]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])
        self.assertEqual(messages[1]["content"], PRIOR_USER)
        self.assertEqual(messages[2]["content"], PRIOR_ANSWER)
        self.assertEqual(messages[-1]["content"], QUESTION)
        source = _source_region(messages[0]["content"])
        self.assertIn(PARENT_TEXT, source)
        self.assertNotIn(PRIOR_USER, source)
        self.assertNotIn(PRIOR_ANSWER, source)
        self.assertNotIn(QUESTION, source)


def _dump(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _source_region(system_text: str) -> str:
    width = DELIMITER_NBYTES * 2
    lines = system_text.splitlines()
    marks = [index for index, line in enumerate(lines) if _delimiter_line(line, width)]
    if len(marks) < 2:
        raise AssertionError("分隔符没有成对出现")
    start, end = marks[-2], marks[-1]
    return "\n".join(lines[start + 1 : end])


def _delimiter_line(line: str, width: int) -> bool:
    if len(line) != width:
        return False
    return all(char in "0123456789abcdef" for char in line)


if __name__ == "__main__":
    unittest.main()
