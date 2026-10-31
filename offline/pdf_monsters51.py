"""从英文 5.1 的怪物章切出怪物。

5.1 没有 5.2 那种标题。怪物名是单独一行加粗，下一行是体型；
少数属性被收进同一行，例如 **Ancient Red Dragon** Gargantuan dragon。
"""

import re

from html_entries import CatalogEntry
from pdf_entries import english_entry

_BOLD = re.compile(r"^\*\*(?P<name>[^*]+)\*\*(?P<rest>.*)$")
_HEADING = re.compile(r"^#{1,2} (?P<name>.+)$")
_SIZE = re.compile(r"^(Tiny|Small|Medium|Large|Huge|Gargantuan)\b", re.IGNORECASE)
_SECTION = re.compile(r"^#{1,2} Monsters \([A-Z]\)$")
_APPENDIX = "# Appendix PH-A"
_CREATURE_APPENDIX = "# Appendix MM-A"
_STAT_SECTIONS = frozenset({
    "traits", "actions", "bonus actions", "reactions", "legendary actions",
    "lair actions", "regional effects", "mythic actions",
})
_NAME_MAX = 60
_LOOKAHEAD = 2


def monster_entries(
    text: str,
    *,
    version_token: str,
    rule_version: str,
    source_id: str,
    source_pin: str,
    pdf_name: str,
) -> list[CatalogEntry]:
    """从怪物章和 MM 附录切。加粗名或 ## 标题后面紧跟体型才是怪物。"""
    lines = text.splitlines()
    fields = {
        "version_token": version_token,
        "rule_version": rule_version,
        "source_id": source_id,
        "source_pin": source_pin,
        "pdf_name": pdf_name,
    }
    entries: list[CatalogEntry] = []
    for start_at, end_at in _regions(lines):
        entries.extend(_region_entries(lines, start_at, end_at, fields))
    return entries


def _regions(lines: list[str]) -> list[tuple[int, int]]:
    """主怪物章停在状态附录。动物和 NPC 在后面的 MM 附录里。"""
    regions: list[tuple[int, int]] = []
    start = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if start is None and _SECTION.match(stripped):
            start = index
        elif start is not None and stripped.startswith(_APPENDIX):
            regions.append((start, index))
            start = None
        elif stripped.startswith(_CREATURE_APPENDIX):
            regions.append((index, len(lines)))
            break
    if start is not None:
        regions.append((start, len(lines)))
    return regions


def _region_entries(
    lines: list[str],
    start_at: int,
    end_at: int,
    fields: dict[str, str],
) -> list[CatalogEntry]:
    starts = [
        index for index in range(start_at + 1, end_at)
        if _is_monster_start(lines, index)
    ]
    entries: list[CatalogEntry] = []
    for position, start in enumerate(starts):
        next_monster = starts[position + 1] if position + 1 < len(starts) else end_at
        end = _body_end(lines, start, next_monster)
        name = _entry_name(lines[start])
        body = "\n".join(lines[start:end]).strip()
        if not name or not body:
            continue
        entries.append(english_entry(
            entry_type="monster",
            english_name=name,
            line_number=start + 1,
            text=body,
            **fields,
        ))
    return entries


def _body_end(lines: list[str], start: int, next_monster: int) -> int:
    """Actions 和 Legendary Actions 留在怪物里。分组标题结束这一条。"""
    for index in range(start + 1, next_monster):
        other = _heading_name(lines[index])
        if other and other.lower() not in _STAT_SECTIONS:
            return index
    return next_monster


def _is_monster_start(lines: list[str], index: int) -> bool:
    if _is_bold_monster(lines, index):
        return True
    return _is_heading_monster(lines, index)


def _is_bold_monster(lines: list[str], index: int) -> bool:
    name, rest = _split_bold(lines[index])
    if not name:
        return False
    if rest:
        return _SIZE.match(rest) is not None
    return _followed_by_size(lines, index)


def _is_heading_monster(lines: list[str], index: int) -> bool:
    """有的属性块用 ## 怪物名，下一行才是体型。字母分节不是怪物。"""
    name = _heading_name(lines[index])
    if not name or name.lower() in _STAT_SECTIONS or _SECTION.match(lines[index].strip()):
        return False
    return _followed_by_size(lines, index)


def _entry_name(line: str) -> str:
    name, _rest = _split_bold(line)
    return name or _heading_name(line)


def _split_bold(line: str) -> tuple[str, str]:
    stripped = line.strip()
    matched = _BOLD.match(stripped)
    if matched:
        name = matched.group("name").strip()
        rest = matched.group("rest").strip()
        # 同一行后半段是体型时，名字里不能再夹着星号。
        if _accept_name(name) and "*" not in name and (not rest or _SIZE.match(rest)):
            return name, rest
    return _starred_name(stripped), ""


def _accept_name(name: str) -> bool:
    # 特质是 **Bite.** 这种带句号的标签，不是怪物名。
    return bool(name) and "." not in name and len(name) <= _NAME_MAX


def _starred_name(line: str) -> str:
    """文字层会把连字符切成 ****，例如 **Saber-****-Toothed Tiger**。"""
    if not (line.startswith("**") and line.endswith("**") and line.count("*") > 4):
        return ""
    collapsed = re.sub(r"-{2,}", "-", line.replace("*", "")).strip()
    if not _accept_name(collapsed):
        return ""
    return collapsed


def _followed_by_size(lines: list[str], index: int) -> bool:
    checked = 0
    for cursor in range(index + 1, len(lines)):
        line = lines[cursor].strip()
        if not line:
            continue
        checked += 1
        if checked > _LOOKAHEAD:
            return False
        return _SIZE.match(line) is not None
    return False


def _heading_name(line: str) -> str:
    stripped = line.strip()
    if not stripped.startswith("#"):
        return ""
    return stripped.lstrip("#").strip()
