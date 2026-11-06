"""生成结果里可以由代码判定的项目。引用是否被原文支持不在这里打分。"""

import json
import re
from dataclasses import dataclass

TYPE_ANSWER = "answer"
TYPE_REFUSAL = "refusal"
QUESTION_MARKS = ("？", "?", "吗")
DICE = re.compile(r"\d+d\d+", re.IGNORECASE)
UNIT_NUMBER = re.compile(r"\d+\s*(?:尺|英尺|gp|xp|环)", re.IGNORECASE)
LONG_NUMBER = re.compile(r"(?<!\d)\d{2,}(?!\d)")
ENTRY_IDENTIFIER = re.compile(r"srd5\d(?::[A-Za-z0-9_-]+)*", re.IGNORECASE)
FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


@dataclass(frozen=True)
class CodeScore:
    """一道题的代码分。structure_ok 为假时其余项不计。"""

    structure_ok: bool
    citation_scope_ok: bool
    numbers_ok: bool
    version_ok: bool
    category_ok: bool
    false_refusal: bool
    require_dm_ok: bool
    require_clarify_ok: bool


def parse_generation(raw: str) -> dict[str, object] | None:
    """去掉代码围栏后读 JSON。字段不全则视为没有结构。"""
    text = FENCE.sub("", raw.strip())
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or not _shape_ok(payload):
        return None
    return payload


def score_generation(
    payload: dict[str, object] | None,
    expected_type: str,
    expected_category: str | None,
    require_dm: bool,
    require_clarify: bool,
    allowed_entry_ids: set[str],
    parent_text: dict[str, str],
) -> CodeScore:
    """结构不合法时，其余检查记为未通过，避免把空答案当成满分。"""
    if payload is None:
        return _failed_structure()
    sentences = payload["sentences"]
    if not isinstance(sentences, list):
        return _failed_structure()
    return _score_payload(
        payload,
        sentences,
        expected_type,
        expected_category,
        require_dm,
        require_clarify,
        allowed_entry_ids,
        parent_text,
    )


def _score_payload(
    payload: dict[str, object],
    sentences: list[object],
    expected_type: str,
    expected_category: str | None,
    require_dm: bool,
    require_clarify: bool,
    allowed_entry_ids: set[str],
    parent_text: dict[str, str],
) -> CodeScore:
    answer_type = str(payload["type"])
    return CodeScore(
        True,
        _citations_ok(sentences, allowed_entry_ids),
        _numbers_ok(sentences, parent_text),
        _versions_ok(sentences),
        _category_ok(answer_type, payload.get("refusal_category"), expected_type, expected_category),
        expected_type == TYPE_ANSWER and answer_type == TYPE_REFUSAL,
        _dm_ok(require_dm, sentences),
        _clarify_ok(require_clarify, sentences),
    )


def _failed_structure() -> CodeScore:
    return CodeScore(
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
    )


def _shape_ok(payload: dict[str, object]) -> bool:
    if payload.get("type") not in (TYPE_ANSWER, TYPE_REFUSAL):
        return False
    sentences = payload.get("sentences")
    if not isinstance(sentences, list):
        return False
    return all(_sentence_ok(item) for item in sentences)


def _sentence_ok(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    text = item.get("text")
    entry_id = item.get("entry_id")
    if not isinstance(text, str) or not text.strip():
        return False
    return entry_id is None or isinstance(entry_id, str)


def _citations_ok(sentences: list[object], allowed_entry_ids: set[str]) -> bool:
    for item in sentences:
        if not isinstance(item, dict):
            return False
        entry_id = item.get("entry_id")
        if entry_id is not None and entry_id not in allowed_entry_ids:
            return False
    return True


def _versions_ok(sentences: list[object]) -> bool:
    for item in sentences:
        if not isinstance(item, dict):
            return False
        entry_id = item.get("entry_id")
        if isinstance(entry_id, str) and not entry_id.startswith("srd51:"):
            return False
    return True


def _numbers_ok(sentences: list[object], parent_text: dict[str, str]) -> bool:
    for item in sentences:
        if not isinstance(item, dict):
            return False
        if not _claims_supported(str(item.get("text", "")), item.get("entry_id"), parent_text):
            return False
    return True


def _claims_supported(text: str, entry_id: object, parent_text: dict[str, str]) -> bool:
    claims = _claims(text)
    if not claims:
        return True
    if not isinstance(entry_id, str):
        return False
    source = _compact(parent_text.get(entry_id, ""))
    return all(_compact(claim) in source for claim in claims)


def _claims(text: str) -> list[str]:
    # 条目编号里的 51 是标识，不是规则数值。
    cleaned = ENTRY_IDENTIFIER.sub(" ", text)
    return DICE.findall(cleaned) + UNIT_NUMBER.findall(cleaned) + LONG_NUMBER.findall(cleaned)


def _dm_ok(require_dm: bool, sentences: list[object]) -> bool:
    if not require_dm:
        return True
    joined = "\n".join(
        str(item.get("text", "")) for item in sentences if isinstance(item, dict)
    )
    return "DM" in joined or "裁定" in joined


def _clarify_ok(require_clarify: bool, sentences: list[object]) -> bool:
    """反问题只有一句，且不引用条目。引用一条规则不能算澄清。"""
    if not require_clarify:
        return True
    if len(sentences) != 1 or not isinstance(sentences[0], dict):
        return False
    sentence = sentences[0]
    text = str(sentence.get("text", ""))
    return sentence.get("entry_id") is None and any(mark in text for mark in QUESTION_MARKS)


def _category_ok(
    answer_type: str,
    category: object,
    expected_type: str,
    expected_category: str | None,
) -> bool:
    if answer_type != expected_type:
        return False
    if expected_category is None:
        return category is None
    return category == expected_category


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()
