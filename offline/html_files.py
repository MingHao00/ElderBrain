"""中文 5.2 里法术和怪物以外的 HTML 怎么切。

一个职业、子职业、物种、背景各是一个文件。专长和魔法物品是一文件多条。
术语速查和法术列表是目录，不入库。
"""

import re
from pathlib import Path

from html_entries import (
    SOURCE_ID_52_ZH,
    SOURCE_PIN_52_ZH,
    CatalogEntry,
    make_catalog_entry,
)
from html_maroon import EntryType, entries_from_maroon_html
from html_text import html_to_text
from ids import RULE_VERSION_52, VERSION_TOKEN_52
from titles import heading_text, split_bilingual_title

SKIP_NAMES = frozenset({"分隔符.htm", "鸣谢列表.htm", "术语速查.htm"})
CLASS_DIR = "角色职业"
FEAT_DIR = "专长"
ITEM_DETAIL = "魔法物品详述"
GLOSSARY_DIR = "术语汇编"
CONDITION_FILE = "状态.htm"
SPECIES_DIR = "种族"
BACKGROUND_DIR = "背景"
GEAR_DIR = "装备"
NAMED_FROM_HEADING = "named_from_heading"
_CHAPTER = re.compile(r"^第.+章")
_HEADING = re.compile(r"<h([123])\b[^>]*>(.*?)</h\1>", re.IGNORECASE | re.DOTALL)
_SPACE = re.compile(r"\s+")
_MIN_BODY_EXTRA = 8

_SINGLE_TYPE = {
    "single-class": "class",
    "single-subclass": "subclass",
    "single-species": "species",
    "single-background": "background",
    "single-rule": "rule",
    "single-equipment": "equipment",
}
_MAROON_TYPE = {
    "maroon-feat": "feat",
    "maroon-item": "equipment",
    "maroon-glossary": "glossary",
    "maroon-rule": "rule",
}


def classify_html(relative: str) -> str:
    """返回 skip、single-* 或 maroon-*。调用方据此决定切法。"""
    name = Path(relative).name
    if _is_skipped_name(name):
        return "skip"
    parts = Path(relative).parts
    class_kind = _class_kind(parts, name)
    if class_kind:
        return class_kind
    if ITEM_DETAIL in relative:
        return "maroon-item"
    if FEAT_DIR in parts:
        return "maroon-feat"
    if GLOSSARY_DIR in parts and name == CONDITION_FILE:
        return "maroon-condition"
    if GLOSSARY_DIR in parts:
        return "maroon-glossary"
    if SPECIES_DIR in parts:
        return "single-species"
    if BACKGROUND_DIR in parts:
        return "single-background"
    if GEAR_DIR in parts and "魔法物品" not in relative:
        return "single-equipment"
    return "single-rule"


def entries_from_classified_html(
    html: str,
    location: str,
) -> tuple[list[CatalogEntry], list[str]]:
    """按路径种类切条目。跳过的文件返回空，不记入未解析。"""
    kind = classify_html(location)
    if kind == "skip":
        return [], []
    if kind in _MAROON_TYPE:
        return _maroon_or_single(html, location, _MAROON_TYPE[kind])
    if kind == "maroon-condition":
        return _maroon_or_single(html, location, _condition_type)
    entry_type = _SINGLE_TYPE[kind]
    return _single_or_empty(html, location, entry_type)


def _is_skipped_name(name: str) -> bool:
    if name in SKIP_NAMES or name.endswith("详述.htm"):
        return True
    if "法术列表" in name:
        return True
    return _CHAPTER.match(Path(name).stem) is not None


def _class_kind(parts: tuple[str, ...], name: str) -> str:
    if CLASS_DIR not in parts:
        return ""
    stem = Path(name).stem
    if stem.endswith("选项"):
        return "maroon-rule"
    if stem == parts[-2]:
        return "single-class"
    return "single-subclass"


def _bilingual_title(html: str) -> tuple[str, str]:
    """先看 H1–H3。没有标题标签时用正文里第一行双语名，例如「陷阱 Traps」。"""
    matched = _HEADING.search(html)
    if matched:
        zh_name, en_name = split_bilingual_title(_clean_heading(matched.group(2)))
        if en_name:
            return zh_name, en_name
    for line in html_to_text(html).splitlines():
        zh_name, en_name = split_bilingual_title(line.strip())
        if en_name:
            return zh_name, en_name
    return "", ""


def _clean_heading(raw: str) -> str:
    return _SPACE.sub(" ", heading_text(raw)).strip()


def _condition_type(raw: str) -> str:
    if "【状态】" in raw:
        return "condition"
    return "glossary"


def _maroon_or_single(
    html: str,
    location: str,
    entry_type: EntryType,
) -> tuple[list[CatalogEntry], list[str]]:
    found, unresolved = entries_from_maroon_html(
        html,
        location_prefix=location,
        entry_type=entry_type,
    )
    if found or unresolved:
        return found, unresolved
    fallback = entry_type if isinstance(entry_type, str) else "rule"
    return _single_or_empty(html, location, fallback)


def _single_or_empty(
    html: str,
    location: str,
    entry_type: str,
) -> tuple[list[CatalogEntry], list[str]]:
    entry, reason = _entry_from_file_heading(html, location, entry_type)
    if entry is not None:
        return [entry], []
    if reason == "no-english":
        return [], [location]
    return [], []


def _entry_from_file_heading(
    html: str,
    location: str,
    entry_type: str,
) -> tuple[CatalogEntry | None, str]:
    zh_name, en_name = _bilingual_title(html)
    if not en_name:
        return None, "no-english" if _HEADING.search(html) else "no-heading"
    text = html_to_text(html)
    title = f"{zh_name} {en_name}".strip()
    if len(_SPACE.sub("", text)) <= len(_SPACE.sub("", title)) + _MIN_BODY_EXTRA:
        return None, "short"
    entry = make_catalog_entry(
        version_token=VERSION_TOKEN_52,
        rule_version=RULE_VERSION_52,
        source_id=SOURCE_ID_52_ZH,
        source_pin=SOURCE_PIN_52_ZH,
        lang="zh",
        entry_type=entry_type,
        english_name=en_name,
        title_zh=zh_name,
        location=f"{location}#{en_name.replace(' ', '_')}",
        text=text,
        align_status=NAMED_FROM_HEADING,
    )
    return entry, ""
