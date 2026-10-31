"""把 5.1 里一文件多条的 Markdown 拆开。

职业文件在唯一的二级标题处切开：前面是职业，后面每个三级标题是一个子职业。
状态、专长、背景的二级标题用 5.2 术语表回查英文名，对不上的不入库。
"""

import re
from pathlib import Path

from html_entries import CatalogEntry, make_catalog_entry
from ids import RULE_VERSION_51, VERSION_TOKEN_51
from reviewed_terms import NAMED_FROM_REVIEW, reviewed_english_name
from markdown_entries import (
    SOURCE_ID_51_ZH,
    SOURCE_PIN_51_ZH,
    first_heading,
)

NAMED_FROM_FILENAME = "named_from_filename"
NAMED_FROM_TERM = "named_from_term"
_H2 = re.compile(r"^## +(.+)$", re.MULTILINE)
_H3 = re.compile(r"^### +(.+)$", re.MULTILINE)

_SECTION_TYPE = {
    "class": "subclass",
    "condition": "condition",
    "feat": "feat",
    "background": "background",
}


def section_entries(
    text: str,
    *,
    relative_path: str,
    kind: str,
    terms: dict[str, dict[str, str]],
) -> tuple[list[CatalogEntry], list[str]]:
    """前言用文件名做英文名。各节用术语表。术语表没有的标题记入未对齐。"""
    if kind == "class":
        preface, sections = _split_class(text)
        preface_type = "class"
    else:
        preface, sections = _split_h2(text)
        preface_type = "rule"
    entries: list[CatalogEntry] = []
    unresolved: list[str] = []
    preface_entry = _from_filename(preface, relative_path, preface_type)
    if preface_entry is not None:
        entries.append(preface_entry)
    section_type = _SECTION_TYPE[kind]
    for heading, body in sections:
        entry = _from_term(heading, body, relative_path, section_type, terms)
        if entry is None:
            unresolved.append(f"{relative_path}#{heading}")
            continue
        entries.append(entry)
    return entries, unresolved


def _split_h2(text: str) -> tuple[str, list[tuple[str, str]]]:
    matches = list(_H2.finditer(text))
    if not matches:
        return text.strip(), []
    preface = text[:matches[0].start()].strip()
    sections = [_span(text, matches, index) for index in range(len(matches))]
    return preface, sections


def _split_class(text: str) -> tuple[str, list[tuple[str, str]]]:
    """二级标题是「武学原型」这类栏位名，子职业从它后面的三级标题开始。"""
    archetype = _H2.search(text)
    if not archetype:
        return text.strip(), []
    tail = text[archetype.start():]
    matches = list(_H3.finditer(tail))
    sections = [_span(tail, matches, index) for index in range(len(matches))]
    return text[:archetype.start()].strip(), sections


def _span(text: str, matches: list[re.Match[str]], index: int) -> tuple[str, str]:
    match = matches[index]
    end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
    return match.group(1).strip(), text[match.start():end].strip()


def _from_filename(text: str, relative_path: str, entry_type: str) -> CatalogEntry | None:
    if not text.strip():
        return None
    english_name = Path(relative_path).stem
    return _make(
        entry_type=entry_type,
        english_name=english_name,
        title_zh=first_heading(text),
        location=f"{relative_path}#{english_name}",
        text=text.strip(),
        align_status=NAMED_FROM_FILENAME,
    )


def _from_term(
    heading: str,
    body: str,
    relative_path: str,
    entry_type: str,
    terms: dict[str, dict[str, str]],
) -> CatalogEntry | None:
    english_name, align_status = _lookup_english(entry_type, heading, terms)
    if not english_name or not body.strip():
        return None
    return _make(
        entry_type=entry_type,
        english_name=english_name,
        title_zh=heading,
        location=f"{relative_path}#{heading}",
        text=body,
        align_status=align_status,
    )


def _lookup_english(
    entry_type: str,
    heading: str,
    terms: dict[str, dict[str, str]],
) -> tuple[str, str]:
    """术语表优先。表里没有、但已经核对过的旧译名用核对结果。"""
    from_terms = terms.get(entry_type, {}).get(heading, "")
    if from_terms:
        return from_terms, NAMED_FROM_TERM
    reviewed = reviewed_english_name(entry_type, heading)
    if reviewed:
        return reviewed, NAMED_FROM_REVIEW
    return "", ""


def _make(
    *,
    entry_type: str,
    english_name: str,
    title_zh: str,
    location: str,
    text: str,
    align_status: str,
) -> CatalogEntry:
    return make_catalog_entry(
        version_token=VERSION_TOKEN_51,
        rule_version=RULE_VERSION_51,
        source_id=SOURCE_ID_51_ZH,
        source_pin=SOURCE_PIN_51_ZH,
        lang="zh",
        entry_type=entry_type,
        english_name=english_name,
        title_zh=title_zh,
        location=location,
        text=text,
        align_status=align_status,
    )
