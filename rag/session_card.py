"""一条对话的会话卡片。标题参与检索，正文不进规则原文。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from config import RULE_VERSION

CARD_VERSION_LABEL = "规则版本"
CARD_ENTRY_LABEL = "entry_id"
CARD_CONCEPT_LABEL = "concept_key"
CARD_TITLE_LABEL = "标题"
# 标题另起一行，避免和问题粘成一个词。
QUERY_TITLE_GAP = "\n"
CATEGORY_OUT_OF_SCOPE = "R1"
CATEGORY_NO_EVIDENCE = "R2"
CATEGORY_INJECTION = "R6"


@dataclass(frozen=True)
class CardEntry:
    """上一轮真正引用过的一条。没有的字段留空，不补标题。"""

    entry_id: str
    concept_key: str | None = None
    title: str | None = None
    location: str | None = None


@dataclass(frozen=True)
class SessionCard:
    """一条对话自己的卡片。新对话没有条目，版本仍固定为 5.1。"""

    rule_version: str
    entries: tuple[CardEntry, ...]


def empty_card() -> SessionCard:
    """还没有上一轮引用。版本不从问题里猜。"""
    return SessionCard(RULE_VERSION, ())


def retrieval_query(question: str, card: SessionCard) -> str:
    """把卡片上的标题接进检索查询。没有标题就只用这一问。"""
    titles = [entry.title for entry in card.entries if entry.title]
    if not titles:
        return question
    return question + QUERY_TITLE_GAP + QUERY_TITLE_GAP.join(titles)


def generation_context(card: SessionCard | None) -> str | None:
    """卡片单独成文。没有条目时不增加消息，位置也不写进去。"""
    if card is None or not card.entries:
        return None
    lines = [f"{CARD_VERSION_LABEL}: {card.rule_version}"]
    for entry in card.entries:
        lines.extend(_entry_lines(entry))
    return "\n".join(lines)


def entries_for_citations(
    cited_ids: Sequence[str],
    metadata_by_id: Mapping[str, Mapping[str, str]],
) -> tuple[CardEntry, ...]:
    """只保留这一轮引用到的编号，并按出现顺序去重。"""
    found: list[CardEntry] = []
    seen: set[str] = set()
    for entry_id in cited_ids:
        if not entry_id or entry_id in seen:
            continue
        seen.add(entry_id)
        meta = metadata_by_id.get(entry_id, {})
        found.append(_entry(entry_id, meta))
    return tuple(found)


def entry_payload(entry: CardEntry) -> dict[str, str]:
    """查询接口只交出有值的字段。"""
    payload = {"entry_id": entry.entry_id}
    _put(payload, "concept_key", entry.concept_key)
    _put(payload, "title", entry.title)
    _put(payload, "location", entry.location)
    return payload


def _entry(entry_id: str, meta: Mapping[str, str]) -> CardEntry:
    return CardEntry(
        entry_id,
        _text(meta, "concept_key"),
        _text(meta, "title"),
        _text(meta, "location"),
    )


def _entry_lines(entry: CardEntry) -> list[str]:
    # 位置留在卡片上给接口。写进生成会被当成规则，所以这里不带。
    lines = [f"{CARD_ENTRY_LABEL}: {entry.entry_id}"]
    if entry.concept_key:
        lines.append(f"{CARD_CONCEPT_LABEL}: {entry.concept_key}")
    if entry.title:
        lines.append(f"{CARD_TITLE_LABEL}: {entry.title}")
    return lines


def _text(meta: Mapping[str, str], name: str) -> str | None:
    value = meta.get(name, "")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _put(payload: dict[str, str], name: str, value: str | None) -> None:
    if value:
        payload[name] = value
