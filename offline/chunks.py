"""把中文 5.1 条目切成检索块。

职业、子职业和规则按二级标题成节。其余类型整条是一节。
超长节再切开，同一节的子块共用 section_id。
"""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass

from chunk_spans import Span, h2_sections, has_body, spans_under_limit
from html_entries import CatalogEntry
from ids import slug

TOKENIZER_NAME = "cjk-word-v1"
TOKEN_LIMITS = (512, 1024)
H2_SECTION_TYPES = frozenset({"class", "subclass", "rule"})
PATH_SEPARATOR = " > "
CHUNK_LANG = "zh"
UNPUBLISHED_INDEX = ""
_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[A-Za-z0-9]+")
_LINK = re.compile(r"!\[([^\]]*)\]\([^)]*\)|\[([^\]]+)\]\([^)]*\)")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    entry_id: str
    concept_key: str
    rule_version: str
    source_id: str
    source_pin: str
    lang: str
    is_authoritative: bool
    is_machine_translated: bool
    section_id: str
    location: str
    entry_title_en: str
    entry_title_zh: str
    index_version: str
    entry_path: str
    text: str
    embed_text: str
    start: int
    end: int
    token_count: int


def count_cjk_words(text: str) -> int:
    """一个汉字计 1，一段连续英文或数字计 1。

    嵌入模型还没选定，不能用厂商分词器。这把尺子只用来比较 512 和 1024。
    """
    words = _WORD.findall(_CJK.sub(" ", text))
    return len(_CJK.findall(text)) + len(words)


def strip_links(text: str) -> str:
    """链接只留可见文字。网址和图片路径不进检索。"""
    return _LINK.sub(lambda match: match.group(1) or match.group(2) or "", text)


def chunk_entry(
    entry: CatalogEntry,
    *,
    token_limit: int,
    count_tokens: Callable[[str], int],
) -> list[Chunk]:
    """把一条目录切成检索块。没有正文时返回空列表。"""
    text = strip_links(entry.text)
    chunks: list[Chunk] = []
    used_ids: set[str] = set()
    for heading, span in _sections(entry, text):
        section_id = _section_id(entry.entry_id, heading, used_ids)
        path = _entry_path(entry, heading)
        pieces = spans_under_limit(text, span, token_limit, count_tokens)
        _append_pieces(chunks, entry, text, pieces, section_id, path, heading, count_tokens)
    return chunks


def chunk_entries(
    entries: list[CatalogEntry],
    *,
    token_limit: int,
    count_tokens: Callable[[str], int],
) -> list[Chunk]:
    """按 entry_id 排序后切块，使清单顺序稳定。"""
    chunks: list[Chunk] = []
    for entry in sorted(entries, key=lambda item: item.entry_id):
        chunks.extend(chunk_entry(entry, token_limit=token_limit, count_tokens=count_tokens))
    return chunks


def chunk_list_checksum(chunks: list[Chunk]) -> str:
    """块清单的 sha256。边界或正文变了，校验值就变。"""
    lines = [
        json.dumps(asdict(item), ensure_ascii=False, sort_keys=True)
        for item in chunks
    ]
    payload = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sections(entry: CatalogEntry, text: str) -> list[tuple[str, Span]]:
    if entry.entry_type in H2_SECTION_TYPES:
        return h2_sections(text)
    if not has_body(text):
        return []
    return [("", Span(0, len(text)))]


def _append_pieces(
    chunks: list[Chunk],
    entry: CatalogEntry,
    text: str,
    pieces: list[Span],
    section_id: str,
    path: str,
    heading: str,
    count_tokens: Callable[[str], int],
) -> None:
    for piece in pieces:
        body = text[piece.start:piece.end].strip()
        if not has_body(body):
            continue
        chunks.append(_make_chunk(
            entry, len(chunks), section_id, path, heading, body, piece, count_tokens,
        ))


def _make_chunk(
    entry: CatalogEntry,
    index: int,
    section_id: str,
    path: str,
    heading: str,
    body: str,
    piece: Span,
    count_tokens: Callable[[str], int],
) -> Chunk:
    return Chunk(
        chunk_id=f"{entry.entry_id}#{CHUNK_LANG}#{index}",
        entry_id=entry.entry_id,
        concept_key=entry.concept_key,
        rule_version=entry.rule_version,
        source_id=entry.source_id,
        source_pin=entry.source_pin,
        lang=CHUNK_LANG,
        is_authoritative=True,
        is_machine_translated=True,
        section_id=section_id,
        location=_location(entry, heading),
        entry_title_en=entry.title_en,
        entry_title_zh=entry.title_zh,
        index_version=UNPUBLISHED_INDEX,
        entry_path=path,
        text=body,
        embed_text=f"{path}\n{body}",
        start=piece.start,
        end=piece.end,
        token_count=count_tokens(body),
    )


def _section_id(entry_id: str, heading: str, used: set[str]) -> str:
    if not heading:
        return entry_id
    base = f"{entry_id}#{_section_slug(heading)}"
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _section_slug(heading: str) -> str:
    english = slug(heading)
    if english:
        return english
    compact = re.sub(r"\s+", "-", heading.strip()).strip("-")
    return compact or "section"


def _entry_path(entry: CatalogEntry, heading: str) -> str:
    file_path = entry.location.split("#", 1)[0]
    folder = file_path.split("/")[-2] if "/" in file_path else ""
    parts = [folder, entry.title_en]
    if heading:
        parts.append(heading)
    return PATH_SEPARATOR.join(part for part in parts if part)


def _location(entry: CatalogEntry, heading: str) -> str:
    file_path = entry.location.split("#", 1)[0]
    anchor = heading or entry.title_zh or entry.title_en
    return f"{file_path}#{anchor}"
