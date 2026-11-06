"""从英文 SRD 切出魔法物品。

物品标题的下一行是类别和稀有度，例如 Wondrous item, uncommon。
稀有度表和物品内部的小标题没有这一行，留在同一条里。
"""

import re

from html_entries import CatalogEntry
from pdf_entries import english_entry

_HEADING = re.compile(r"^## (?P<name>.+)$")
_KIND = (
    r"(?:Ammunition|Armor|Potion|Ring|Rod|Scroll|Staff|Wand|Weapon|Wondrous [Ii]tem)"
)
_RARITY = r"(?:uncommon|common|legendary|artifact|varies|rarity|rare)"
_ITEM_TYPE = re.compile(
    rf"^(?:## )?{_KIND}\b.*,.*\b{_RARITY}\b",
    re.IGNORECASE,
)
# 文字层有时把「Wondrous item, uncommon」写进标题同一行，下一行就不再是类别。
_COMBINED = re.compile(
    rf"^(?P<name>.+?) (?P<kind>{_KIND}\b.*,.*\b{_RARITY}\b.*)$",
    re.IGNORECASE,
)
_LOOKAHEAD = 3
# 物品章结束后的章节标题。最后一件物品不能把后面的规则和怪物吞进去。
_CHAPTER_STOPS = frozenset({
    "monsters",
    "sentient magic items",
})
_MONSTER_LETTER = re.compile(r"^monsters \([a-z]\)$", re.IGNORECASE)


def item_entries(
    text: str,
    *,
    version_token: str,
    rule_version: str,
    source_id: str,
    source_pin: str,
    pdf_name: str,
) -> list[CatalogEntry]:
    """按「标题 + 类别稀有度」切开。类别行本身不是一条新物品。"""
    lines = text.splitlines()
    starts = [index for index in range(len(lines)) if _is_item_start(lines, index)]
    entries: list[CatalogEntry] = []
    for position, start in enumerate(starts):
        next_item = starts[position + 1] if position + 1 < len(starts) else len(lines)
        end = _item_end(lines, start, next_item)
        name = _entry_name(lines[start])
        body = "\n".join(lines[start:end]).strip()
        if not name or not body:
            continue
        entries.append(english_entry(
            version_token=version_token,
            rule_version=rule_version,
            source_id=source_id,
            source_pin=source_pin,
            entry_type="equipment",
            english_name=name,
            pdf_name=pdf_name,
            line_number=start + 1,
            text=body,
        ))
    return entries


def _item_end(lines: list[str], start: int, next_item: int) -> int:
    """物品正文停在下一件物品，或魔法物品章之后的章节。"""
    for index in range(start + 1, next_item):
        if _is_chapter_stop(lines[index]):
            return index
    return next_item


def _is_chapter_stop(line: str) -> bool:
    name = _heading_name(line)
    if name.lower() in _CHAPTER_STOPS:
        return True
    # 5.1 的怪物章按字母分节，例如 Monsters (A)。战斗章的 Monsters and Death 不是这里。
    return _MONSTER_LETTER.match(name) is not None


def _is_item_start(lines: list[str], index: int) -> bool:
    name = _heading_name(lines[index])
    if not name or _ITEM_TYPE.match(name):
        return False
    if _COMBINED.match(name):
        return True
    return _ITEM_TYPE.match(_next_nonempty(lines, index)) is not None


def _entry_name(line: str) -> str:
    heading = _heading_name(line)
    combined = _COMBINED.match(heading)
    if combined:
        return combined.group("name").strip()
    return heading


def _heading_name(line: str) -> str:
    matched = _HEADING.match(line.strip())
    if not matched:
        return ""
    return matched.group("name").strip()


def _next_nonempty(lines: list[str], index: int) -> str:
    checked = 0
    for cursor in range(index + 1, len(lines)):
        line = lines[cursor].strip()
        if not line:
            continue
        checked += 1
        if checked > _LOOKAHEAD:
            return ""
        return line
    return ""
