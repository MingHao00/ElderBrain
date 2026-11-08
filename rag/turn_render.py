"""网页逐句和查询 JSON。都在整段引用校验之后才组装。"""

from __future__ import annotations

import json

from auth_constants import HTTP_OK, JSON_TYPE, NDJSON_TYPE
from auth_http import Outgoing
from citation_check import CitedSentence
from scope import VERSION_SENTENCE
from session_card import CardEntry, entry_payload


def render_ask(shown: str, conversation_id: str) -> Outgoing:
    """先推版本句，再推已经通过校验的句子，最后带上对话编号。"""
    lines = [_dump({"event": "version", "text": VERSION_SENTENCE})]
    for sentence in sentences_of(shown):
        lines.append(_dump(_sentence_event(sentence)))
    lines.append(_dump({"event": "done", "conversation_id": conversation_id}))
    return Outgoing(HTTP_OK, "".join(lines).encode("utf-8"), NDJSON_TYPE)


def render_query(shown: str, conversation_id: str) -> Outgoing:
    """查询接口等全部句子都校验完，再一次返回。"""
    payload = {
        "conversation_id": conversation_id,
        "version": VERSION_SENTENCE,
        "sentences": [_sentence_body(item) for item in sentences_of(shown)],
        "citations": [entry_payload(item) for item in citations_of(shown)],
        "refusal_category": refusal_of(shown),
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return Outgoing(HTTP_OK, body, JSON_TYPE)


def sentences_of(shown: str) -> tuple[CitedSentence, ...]:
    """优先用校验后的句子。普通字符串只剩去掉版本句后的一句。"""
    found = getattr(shown, "sentences", None)
    if isinstance(found, tuple) and found:
        return found
    return _from_text(str(shown))


def citations_of(shown: str) -> tuple[CardEntry, ...]:
    """这一轮引用到的条目。没有结构化结果就是空的。"""
    found = getattr(shown, "citations", None)
    if isinstance(found, tuple):
        return found
    return ()


def refusal_of(shown: str) -> str | None:
    """没有拒答类别时给查询接口 null。"""
    found = getattr(shown, "refusal_category", None)
    if isinstance(found, str) and found:
        return found
    return None


def card_update_of(shown: str) -> tuple[CardEntry, ...] | None:
    """拒答和校验失败不带更新。空元组也不覆盖上一轮。"""
    found = getattr(shown, "card_update", None)
    if isinstance(found, tuple) and found:
        return found
    return None


def _from_text(text: str) -> tuple[CitedSentence, ...]:
    body = text.strip()
    if body.startswith(VERSION_SENTENCE):
        body = body[len(VERSION_SENTENCE) :].strip()
    if not body:
        return ()
    return (CitedSentence(body, None),)


def _sentence_event(sentence: CitedSentence) -> dict[str, object]:
    return {
        "event": "sentence",
        "text": sentence.text,
        "entry_id": sentence.entry_id,
    }


def _sentence_body(sentence: CitedSentence) -> dict[str, object]:
    return {"text": sentence.text, "entry_id": sentence.entry_id}


def _dump(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"
