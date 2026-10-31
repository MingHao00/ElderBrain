"""从中文 5.2 HTML 切出法术和怪物。一个 H4 或 H5 是一条。"""

import re
from dataclasses import dataclass
from pathlib import Path

from html_text import html_to_text
from ids import RULE_VERSION_52, VERSION_TOKEN_52, concept_key, entry_id
from monster_names import MONSTERS_ABSENT_FROM_ENGLISH, canonical_monster_name
from reviewed_terms import repair_source_english
from titles import heading_text, split_bilingual_title

SOURCE_ID_52_ZH = "src-srd52-zh"
SOURCE_PIN_52_ZH = "dec9e054585905b40ab66a12cb12ab13416e822d"
SPELL_DIR_NAME = "法术详述"
MONSTER_BOOK_NAME = "怪物图鉴2025"
SKIP_FILE_NAMES = frozenset({"法律信息.htm", "怪物.htm"})
SKIP_DIR_NAMES = frozenset({"前言"})

_HEADING = re.compile(r"<h([45])\b([^>]*)>(.*?)</h\1>", re.IGNORECASE | re.DOTALL)
_ID_ATTR = re.compile(r"""id\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


@dataclass(frozen=True)
class CatalogEntry:
    entry_id: str
    concept_key: str
    rule_version: str
    source_id: str
    source_pin: str
    lang: str
    entry_type: str
    title_en: str
    title_zh: str
    location: str
    text: str
    align_status: str


def read_gbk_html(path: Path) -> str:
    """上游 HTML 声明 gb2312，实际按 gb18030 才能覆盖生僻字。"""
    return path.read_bytes().decode("gb18030")


def _english_from_id(attrs: str) -> str:
    matched = _ID_ATTR.search(attrs)
    if not matched:
        return ""
    return matched.group(1).replace("_", " ").strip()


def make_catalog_entry(
    *,
    version_token: str,
    rule_version: str,
    source_id: str,
    source_pin: str,
    lang: str,
    entry_type: str,
    english_name: str,
    title_zh: str,
    location: str,
    text: str,
    align_status: str,
) -> CatalogEntry:
    """组装一条清单记录。编号由版本、类型和英文名决定。"""
    english_name, align_status = repair_source_english(english_name, align_status)
    if entry_type == "monster" and lang == "zh":
        english_name, align_status = canonical_monster_name(english_name, align_status)
    return CatalogEntry(
        entry_id=entry_id(version_token, entry_type, english_name),
        concept_key=concept_key(entry_type, english_name),
        rule_version=rule_version,
        source_id=source_id,
        source_pin=source_pin,
        lang=lang,
        entry_type=entry_type,
        title_en=english_name,
        title_zh=title_zh,
        location=location,
        text=text,
        align_status=align_status,
    )


def _entries_from_html(
    html: str,
    *,
    level: str,
    entry_type: str,
    location_prefix: str,
) -> tuple[list[CatalogEntry], list[str]]:
    matches = [item for item in _HEADING.finditer(html) if item.group(1) == level]
    entries: list[CatalogEntry] = []
    unresolved: list[str] = []
    for index, match in enumerate(matches):
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(html)
        zh_name, en_name = split_bilingual_title(match.group(3))
        if not en_name:
            en_name = _english_from_id(match.group(2))
        title = heading_text(match.group(3))
        body = html_to_text(html[match.end():body_end])
        if not en_name or not body:
            unresolved.append(f"{location_prefix}#{title}")
            continue
        anchor = _english_from_id(match.group(2)) or en_name
        entries.append(make_catalog_entry(
            version_token=VERSION_TOKEN_52,
            rule_version=RULE_VERSION_52,
            source_id=SOURCE_ID_52_ZH,
            source_pin=SOURCE_PIN_52_ZH,
            lang="zh",
            entry_type=entry_type,
            english_name=en_name,
            title_zh=zh_name,
            location=f"{location_prefix}#{anchor.replace(' ', '_')}",
            text=f"{title}\n{body}",
            align_status="named_from_heading",
        ))
    return entries, unresolved


def entries_from_spell_html(html: str, location_prefix: str) -> tuple[list[CatalogEntry], list[str]]:
    """法术详述按 H4 一条法术切开。"""
    return _entries_from_html(html, level="4", entry_type="spell", location_prefix=location_prefix)


def entries_from_monster_html(html: str, location_prefix: str) -> tuple[list[CatalogEntry], list[str]]:
    """怪物图鉴按 H5 一条怪物切开。属性块留在同一条里。"""
    entries, unresolved = _entries_from_html(
        html, level="5", entry_type="monster", location_prefix=location_prefix,
    )
    return _drop_absent_monsters(entries, unresolved)


def _drop_absent_monsters(
    entries: list[CatalogEntry],
    unresolved: list[str],
) -> tuple[list[CatalogEntry], list[str]]:
    """英文 5.2 没有的怪物不入库，只留在失败清单。"""
    kept: list[CatalogEntry] = []
    skipped = list(unresolved)
    for entry in entries:
        if entry.title_en not in MONSTERS_ABSENT_FROM_ENGLISH:
            kept.append(entry)
            continue
        skipped.append(f"{entry.location}#no-english:{entry.title_en}")
    return kept, skipped


def is_skipped_html(path: Path) -> bool:
    if path.name in SKIP_FILE_NAMES:
        return True
    return any(part in SKIP_DIR_NAMES for part in path.parts)
