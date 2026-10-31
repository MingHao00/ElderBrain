"""从整理后的英文 SRD 正文切出法术。一条的开头是加粗法术名加环阶。"""

import re

from html_entries import CatalogEntry
from ids import concept_key, entry_id

SOURCE_ID_51_EN = "src-srd51-en"
SOURCE_PIN_51_EN = "2504D2A0ABB0A4D491A939BE4F17910A2DDE0312570AB8D208080225CCF0A1F0"
SOURCE_ID_521_EN = "src-srd521-en"
SOURCE_PIN_521_EN = "8974902D109D6E63672D7C490BDE9CCF052410503D9CFA768237154FBC5E3D87"
ENTRY_TYPE_SPELL = "spell"
LANG_EN = "en"
ALIGN_FROM_HEADING = "named_from_heading"

_SCHOOLS = (
    "abjuration|conjuration|divination|enchantment|"
    "evocation|illusion|necromancy|transmutation"
)
_SCHOOLS_52 = (
    "Abjuration|Conjuration|Divination|Enchantment|"
    "Evocation|Illusion|Necromancy|Transmutation"
)
_META = (
    rf"(?:\d{{1,2}}(?:st|nd|rd|th)-level (?:{_SCHOOLS})(?: \(ritual\))?"
    rf"|Level \d{{1,2}} (?:{_SCHOOLS_52})"
    rf"|(?:{_SCHOOLS_52}) [Cc]antrip)"
)
GARBLED_TITLE = "garbled_title"
REPAIRED_HEADING = "repaired_heading"
# 这五条的文字层丢了字母。环阶、学派和正文对得上 SRD 法术，只改标题。
REPAIRED_TITLES = {
    "Ci l f D h rc e o eat": "Circle of Death",
    "E th k ar qua e": "Earthquake",
    "E h Abili n ance ty": "Enhance Ability",
    "E bit ye e": "Eyebite",
    "F ear": "Fear",
}
_JOINED = re.compile(rf"^\*\*(?P<name>[A-Z][^*]+?)\*\* (?P<meta>{_META})(?:\s|$)")
_NAME_ONLY = re.compile(r"^\*\*(?P<name>[A-Z][^*]+)\*\*$")
_HEADING = re.compile(r"^## (?P<name>[A-Z].+)$")
_HEADING_JOINED = re.compile(rf"^## (?P<name>[A-Z].+?) (?P<meta>{_META})$")
_META_LINE = re.compile(rf"^(?P<meta>{_META})(?:\s|$)")


def spell_header_pattern(title: str) -> re.Pattern[str]:
    """行首的法术名加环阶。5.1 用 3rd-level，5.2 用 Level 3。"""
    return re.compile(rf"(?:^|\n){re.escape(title)}\s+{_META}")


def spell_entries(
    text: str,
    *,
    version_token: str,
    rule_version: str,
    source_id: str,
    source_pin: str,
    pdf_name: str,
) -> list[CatalogEntry]:
    """按法术标题行切开。施法时间等加粗字段留在正文里。"""
    lines = text.splitlines()
    starts = [index for index in range(len(lines)) if _spell_name(lines, index)]
    entries: list[CatalogEntry] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        name = _spell_name(lines, start)
        if name is None:
            continue
        body = "\n".join(lines[start:end]).strip()
        if not body:
            continue
        entries.append(_entry(
            name=name,
            body=body,
            line_number=start + 1,
            version_token=version_token,
            rule_version=rule_version,
            source_id=source_id,
            source_pin=source_pin,
            pdf_name=pdf_name,
        ))
    return entries


def _spell_name(lines: list[str], index: int) -> str | None:
    """同一行或下一行带环阶的加粗名才是法术。小节名没有环阶。"""
    for pattern in (_JOINED, _HEADING_JOINED):
        joined = pattern.match(lines[index])
        if joined:
            return joined.group("name").strip()
    for pattern in (_NAME_ONLY, _HEADING):
        matched = pattern.match(lines[index])
        if matched and index + 1 < len(lines) and _META_LINE.match(lines[index + 1]):
            return matched.group("name").strip()
    return None


def _entry(
    *,
    name: str,
    body: str,
    line_number: int,
    version_token: str,
    rule_version: str,
    source_id: str,
    source_pin: str,
    pdf_name: str,
) -> CatalogEntry:
    title, status = _title_status(name)
    return CatalogEntry(
        entry_id=entry_id(version_token, ENTRY_TYPE_SPELL, title),
        concept_key=concept_key(ENTRY_TYPE_SPELL, title),
        rule_version=rule_version,
        source_id=source_id,
        source_pin=source_pin,
        lang=LANG_EN,
        entry_type=ENTRY_TYPE_SPELL,
        title_en=title,
        title_zh="",
        location=f"{pdf_name}#line:{line_number}",
        text=body,
        align_status=status,
    )


def _title_status(name: str) -> tuple[str, str]:
    repaired = REPAIRED_TITLES.get(name)
    if repaired:
        return repaired, REPAIRED_HEADING
    if _is_garbled(name):
        return name, GARBLED_TITLE
    return name, ALIGN_FROM_HEADING


def _is_garbled(name: str) -> bool:
    """文字层把标题拆成单字母时，不能当成英文名。"""
    return any(len(token.strip("'-")) <= 1 for token in name.split())
