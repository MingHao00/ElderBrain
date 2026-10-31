"""从英文 5.2 的怪物章切出怪物。属性块、特质和动作留在同一条里。"""

import re

from html_entries import CatalogEntry
from pdf_entries import english_entry

_HEADING = re.compile(r"^#{1,2} (?P<name>.+)$")
_SIZE = re.compile(r"^(Tiny|Small|Medium|Large|Huge|Gargantuan)\b", re.IGNORECASE)
# 文字层有时把整块属性收进一个 HTML 单元格，体型不在行首。
_SIZE_IN_CELL = re.compile(
    r"<t[dh][^>]*>\s*(?:Tiny|Small|Medium|Large|Huge|Gargantuan)\b",
    re.IGNORECASE,
)
# 没有 # 的怪物名，例如连续两行 “Axe Beak”。句子和带标点的属性行排除在外。
_PLAIN_NAME = re.compile(r"^[A-Z][A-Za-z'’\-]+(?: [A-Z][A-Za-z'’\-]+)*$")
_PLAIN_NAME_MAX = 40
_SECTION_STARTS = ("## Monsters A–Z", "## Monsters A-Z")
_STAT_SECTIONS = frozenset({
    "traits", "actions", "bonus actions", "reactions", "legendary actions",
    "lair actions", "regional effects", "mythic actions",
})
# 这些词被误标成标题时仍属于当前怪物，不是下一条的名字。
_INLINE_STAT_LABELS = frozenset({
    "senses", "skills", "resistances", "immunities", "vulnerabilities",
    "languages", "gear", "cr", "speed",
})
_NOT_MONSTER = _STAT_SECTIONS | frozenset({"animals"})
_LOOKAHEAD = 4


def monster_entries(
    text: str,
    *,
    version_token: str,
    rule_version: str,
    source_id: str,
    source_pin: str,
    pdf_name: str,
) -> list[CatalogEntry]:
    """从 Monsters A–Z 起切。后面紧跟体型的名字才是一条怪物。"""
    lines = text.splitlines()
    section = _section_start(lines)
    if section is None:
        return []
    starts = [
        index for index in range(section + 1, len(lines))
        if _is_monster_start(lines, index)
    ]
    entries: list[CatalogEntry] = []
    for position, start in enumerate(starts):
        next_monster = starts[position + 1] if position + 1 < len(starts) else len(lines)
        end = _body_end(lines, start, next_monster)
        name = _monster_name(lines[start])
        body = "\n".join(lines[start:end]).strip()
        if not name or not body:
            continue
        entries.append(english_entry(
            version_token=version_token,
            rule_version=rule_version,
            source_id=source_id,
            source_pin=source_pin,
            entry_type="monster",
            english_name=name,
            pdf_name=pdf_name,
            line_number=start + 1,
            text=body,
        ))
    return entries


def _body_end(lines: list[str], start: int, next_monster: int) -> int:
    """属性块里的 Traits、Actions 留在怪物里。别的标题结束这一条。"""
    name = _monster_name(lines[start])
    for index in range(start + 1, next_monster):
        other = _heading_name(lines[index])
        if not other or other == name or _is_stat_heading(other):
            continue
        return index
    return next_monster


def _section_start(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        if line.strip() in _SECTION_STARTS:
            return index
    return None


def _is_monster_start(lines: list[str], index: int) -> bool:
    name = _monster_name(lines[index])
    if not name or _is_reserved_name(name) or _SIZE.match(name):
        return False
    # 标题下面再印一遍名字，或表格把体型包起来，都还是同一条。
    if _recent_name(lines, index) == name:
        return False
    return _followed_by_size(lines, index, name)


def _monster_name(line: str) -> str:
    return _heading_name(line) or _single_cell(line) or _plain_name(line)


def _plain_name(line: str) -> str:
    stripped = line.strip()
    if len(stripped) > _PLAIN_NAME_MAX or not _PLAIN_NAME.match(stripped):
        return ""
    return stripped


def _is_reserved_name(name: str) -> bool:
    lowered = name.lower()
    if lowered in _NOT_MONSTER:
        return True
    return lowered.split(" ", 1)[0] in _INLINE_STAT_LABELS


def _is_stat_heading(name: str) -> bool:
    return _is_reserved_name(name) and name.lower() != "animals"


def _single_cell(line: str) -> str:
    """只认单独一格的表头，例如 | Wolf |。多列表是随机表，不是怪物名。"""
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")):
        return ""
    parts = [part.strip() for part in stripped.strip("|").split("|") if part.strip()]
    if len(parts) != 1 or set(parts[0]) <= set("-: "):
        return ""
    return parts[0]


def _recent_name(lines: list[str], index: int) -> str:
    """往上找到上一条怪物名。表格分隔线和正文不是名字。"""
    for cursor in range(index - 1, -1, -1):
        line = lines[cursor].strip()
        if not line or _is_table_rule(line):
            continue
        name = _monster_name(line)
        if name and not _SIZE.match(name):
            return name
    return ""


def _followed_by_size(lines: list[str], index: int, name: str) -> bool:
    checked = 0
    for cursor in range(index + 1, len(lines)):
        raw = lines[cursor].strip()
        if not raw or _is_table_rule(raw) or _repeats_name(raw, name):
            continue
        content = _single_cell(raw) or raw
        checked += 1
        if checked > _LOOKAHEAD:
            return False
        if _SIZE.match(content) or _SIZE_IN_CELL.search(raw):
            return True
        # 体型有时在下一行，中间夹着别的单行表格。普通段落则不是怪物。
        if not raw.startswith("|"):
            return False
    return False


def _is_table_rule(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and not _single_cell(stripped)


def _repeats_name(line: str, name: str) -> bool:
    """文字层会把怪物名再印一遍，标题、单格或普通行都算。"""
    if line.strip() == name:
        return True
    return _heading_name(line) == name or _single_cell(line) == name


def _heading_name(line: str) -> str:
    matched = _HEADING.match(line.strip())
    if not matched:
        return ""
    return matched.group("name").strip()
