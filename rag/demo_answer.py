"""把生成结果收成页面上的纯文本。页面不接触密钥。"""

import json

from generation_score import parse_generation

MAX_QUESTION_CHARS = 2_000
MAX_HISTORY_MESSAGES = 8
MAX_HISTORY_CHARS = 2_000
# 历史字段已不再使用，但请求里仍可能夹带。上限沿用原来的预算，避免一次读进过大的 JSON。
_UTF8_BYTES_PER_CHAR = 3
_JSON_OVERHEAD_BYTES = 4_096
_MAX_TEXT_CHARS = MAX_QUESTION_CHARS + MAX_HISTORY_MESSAGES * MAX_HISTORY_CHARS
MAX_BODY_BYTES = _MAX_TEXT_CHARS * _UTF8_BYTES_PER_CHAR + _JSON_OVERHEAD_BYTES
REFUSAL_LINES = {
    "R1": "所问内容不在已收录的中文 SRD 5.1 里。",
    "R2": "原文不够，写不出这条规则。",
    "R3": "这不是规则问题。",
    "R5": "这里只做规则查询。",
}


def read_question(raw: bytes) -> str:
    """从 JSON 正文取出问题。过长或空问题直接拒绝。"""
    question, _conversation_id = read_turn(raw)
    return question


def read_turn(raw: bytes) -> tuple[str, str | None]:
    """取出当前问题和可选的对话编号。历史字段即使带上也不使用。"""
    if len(raw) > MAX_BODY_BYTES:
        raise ValueError("问题太长")
    payload = _object(raw)
    return _question_text(payload), _conversation_id(payload)


def _question_text(payload: dict[str, object]) -> str:
    question = payload.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("请写下问题")
    text = question.strip()
    if len(text) > MAX_QUESTION_CHARS:
        raise ValueError("问题太长")
    return text


def _conversation_id(payload: dict[str, object]) -> str | None:
    # 空字符串和别的类型都当成没带编号，由服务端新建。不接受客户端自己编号。
    raw = payload.get("conversation_id")
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    return text


def present_answer(raw: str) -> str:
    """版本句保留。模型 JSON 拆成句子，普通拒答原文照样显示。"""
    head, _, tail = raw.partition("\n\n")
    payload = parse_generation(tail if tail else raw)
    if payload is None:
        return raw
    lines = _sentence_lines(payload)
    if lines:
        return _join(head if tail else "", lines)
    if payload.get("type") != "refusal":
        return raw
    note = REFUSAL_LINES.get(str(payload.get("refusal_category")), "不能回答这个问题。")
    return _join(head if tail else "", [note])


def _object(raw: bytes) -> dict[str, object]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("请求不是 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("请求不是 JSON")
    return payload


def _sentence_lines(payload: dict[str, object]) -> list[str]:
    sentences = payload.get("sentences")
    if not isinstance(sentences, list):
        return []
    lines: list[str] = []
    for item in sentences:
        line = _sentence_line(item)
        if line:
            lines.append(line)
    return lines


def _sentence_line(item: object) -> str:
    if not isinstance(item, dict):
        return ""
    text = str(item.get("text", "")).strip()
    entry_id = item.get("entry_id")
    if text and isinstance(entry_id, str) and entry_id:
        return f"{text}\n（{entry_id}）"
    return text


def _join(head: str, lines: list[str]) -> str:
    body = "\n\n".join(lines)
    if head and body:
        return f"{head}\n\n{body}"
    return head or body
