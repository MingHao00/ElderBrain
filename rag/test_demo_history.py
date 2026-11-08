"""请求里的历史不使用。会话卡片不进入规则原文。"""

import json
import unittest

from config import RULE_VERSION
from demo_answer import read_turn
from modules.data_preparation import RagPiece
from modules.generation import DELIMITER_NBYTES, GenerationIntegrationModule
from session_card import CardEntry, SessionCard, empty_card
from test_api_embeddings import _ChatClient

QUESTION = "上一句说的是哪个法术"
PRIOR_ANSWER = "三环法术，射程一百五十尺。"
PARENT_TEXT = "原文只写射程规则。"
CARD_TITLE = "卡片专有标题"
CONCEPT = "spell.fireball"
ENTRY = "srd51:spell:fireball"
LOCATION = "docs/hidden-location.md"


class TurnRequestTests(unittest.TestCase):
    def test_reads_question_and_conversation_id(self) -> None:
        payload = {
            "question": "  " + QUESTION + "  ",
            "conversation_id": "  c_server  ",
        }
        question, conversation_id = read_turn(_dump(payload))
        self.assertEqual(question, QUESTION)
        self.assertEqual(conversation_id, "c_server")

    def test_missing_id_means_a_new_conversation(self) -> None:
        raw = '{"question": "火球术射程"}'.encode("utf-8")
        question, conversation_id = read_turn(raw)
        self.assertEqual(question, "火球术射程")
        self.assertIsNone(conversation_id)
        blank = '{"question": "火球术射程", "conversation_id": "  "}'.encode("utf-8")
        self.assertIsNone(read_turn(blank)[1])

    def test_history_is_ignored(self) -> None:
        payload = {
            "question": "射程",
            "conversation_id": "c_kept",
            "history": [{"role": "system", "text": PRIOR_ANSWER}],
        }
        question, conversation_id = read_turn(_dump(payload))
        self.assertEqual(question, "射程")
        self.assertEqual(conversation_id, "c_kept")


class CardMessageTests(unittest.TestCase):
    def test_card_stays_outside_the_source(self) -> None:
        client = _ChatClient("火球术")
        module = GenerationIntegrationModule(0, 32, client, "test-model")
        parent = RagPiece(
            PARENT_TEXT,
            {"entry_id": ENTRY, "location": "docs/Spells/Fireball.md"},
        )
        card = SessionCard(
            RULE_VERSION,
            (CardEntry(ENTRY, CONCEPT, CARD_TITLE, LOCATION),),
        )
        self.assertEqual(module.answer(QUESTION, [parent], card), "火球术")
        messages = client.chat.completions.messages
        roles = [item["role"] for item in messages]
        self.assertEqual(roles, ["system", "system", "user"])
        self.assertEqual(messages[-1]["content"], QUESTION)
        self.assertIn(CARD_TITLE, messages[1]["content"])
        self.assertIn(CONCEPT, messages[1]["content"])
        self.assertIn(RULE_VERSION, messages[1]["content"])
        self.assertNotIn(LOCATION, messages[1]["content"])
        self.assertNotIn(PRIOR_ANSWER, messages[1]["content"])
        source = _source_region(messages[0]["content"])
        self.assertIn(PARENT_TEXT, source)
        self.assertNotIn(CARD_TITLE, source)
        self.assertNotIn(CONCEPT, source)
        self.assertNotIn(QUESTION, source)

    def test_empty_card_does_not_add_a_message(self) -> None:
        client = _ChatClient("火球术")
        module = GenerationIntegrationModule(0, 32, client, "test-model")
        parent = RagPiece(PARENT_TEXT, {"entry_id": ENTRY, "location": "docs/x.md"})
        self.assertEqual(module.answer(QUESTION, [parent], empty_card()), "火球术")
        roles = [item["role"] for item in client.chat.completions.messages]
        self.assertEqual(roles, ["system", "user"])


def _dump(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _source_region(system_text: str) -> str:
    width = DELIMITER_NBYTES * 2
    lines = system_text.splitlines()
    marks = [index for index, line in enumerate(lines) if _delimiter_line(line, width)]
    if len(marks) < 2:
        raise AssertionError("分隔符没有成对出现")
    return "\n".join(lines[marks[-2] + 1 : marks[-1]])


def _delimiter_line(line: str, width: int) -> bool:
    if len(line) != width:
        return False
    return all(char in "0123456789abcdef" for char in line)


if __name__ == "__main__":
    unittest.main()
