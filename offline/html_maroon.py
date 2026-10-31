"""从栗色标题切出一条中文 5.2 条目。

专长、魔法物品和术语汇编没有稳定的 H4。条目标题是 color=#800000 的双语名。
紫色字是正文里的法术名，不能当成新条目。
"""

import re
from collections.abc import Callable

from html_entries import (
    SOURCE_ID_52_ZH,
    SOURCE_PIN_52_ZH,
    CatalogEntry,
    make_catalog_entry,
)
from html_text import html_to_text
from ids import RULE_VERSION_52, VERSION_TOKEN_52, slug
from titles import heading_text, split_bilingual_title

NAMED_FROM_HEADING = "named_from_heading"
_MAROON = "800000"
_LOOKBACK = 180
_MIN_BODY_EXTRA = 8
_FONT = re.compile(r"<font\b([^>]*)>(.*?)</font>", re.IGNORECASE | re.DOTALL)
_COLOR = re.compile(rf"color\s*=\s*[\"']?#?{_MAROON}\b", re.IGNORECASE)
_MARK = re.compile(r"【[^】]*】")
_SPACE = re.compile(r"\s+")
_ID_ATTR = re.compile(r"""id\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_COMPACT = re.compile(r"\s+")

EntryType = str | Callable[[str], str]


def entries_from_maroon_html(
    html: str,
    *,
    location_prefix: str,
    entry_type: EntryType,
) -> tuple[list[CatalogEntry], list[str]]:
    """按栗色双语标题切开。没有英文名、或只有标题没有正文的不入库。"""
    spans = _maroon_spans(html)
    entries: list[CatalogEntry] = []
    unresolved: list[str] = []
    for index, (start, _end, raw, zh_name, en_name) in enumerate(spans):
        body_end = spans[index + 1][0] if index + 1 < len(spans) else len(html)
        text = html_to_text(html[start:body_end])
        title = f"{zh_name} {en_name}".strip()
        if not _has_body(text, title):
            unresolved.append(f"{location_prefix}#{title}")
            continue
        kind = entry_type(raw) if callable(entry_type) else entry_type
        anchor = _anchor_before(html, start, slug(en_name))
        entries.append(make_catalog_entry(
            version_token=VERSION_TOKEN_52,
            rule_version=RULE_VERSION_52,
            source_id=SOURCE_ID_52_ZH,
            source_pin=SOURCE_PIN_52_ZH,
            lang="zh",
            entry_type=kind,
            english_name=en_name,
            title_zh=zh_name,
            location=f"{location_prefix}#{anchor}",
            text=text,
            align_status=NAMED_FROM_HEADING,
        ))
    return entries, unresolved


def _maroon_spans(html: str) -> list[tuple[int, int, str, str, str]]:
    spans: list[tuple[int, int, str, str, str]] = []
    for match in _FONT.finditer(html):
        if not _COLOR.search(match.group(1)):
            continue
        raw = heading_text(match.group(2))
        cleaned = _SPACE.sub(" ", _MARK.sub("", raw)).strip(" 。.")
        zh_name, en_name = split_bilingual_title(cleaned)
        if not en_name:
            continue
        spans.append((match.start(), match.end(), raw, zh_name, en_name))
    return spans


def _anchor_before(html: str, start: int, fallback: str) -> str:
    """锚点写在包住标题的 p 上。再往前会误用上一条的 id。"""
    window = html[max(0, start - _LOOKBACK):start]
    paragraph = window.lower().rfind("<p")
    if paragraph < 0:
        return fallback
    matched = _ID_ATTR.search(window[paragraph:paragraph + _LOOKBACK])
    if not matched:
        return fallback
    return matched.group(1)


def _has_body(text: str, title: str) -> bool:
    compact = _COMPACT.sub("", text)
    title_compact = _COMPACT.sub("", title)
    return len(compact) > len(title_compact) + _MIN_BODY_EXTRA
