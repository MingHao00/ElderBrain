"""从英文 PDF 切职业、子职业、背景、物种和专长。

5.1 四个子职业的标题是旧写法。2026-10-31 已确认它们对应 5.2 的英文名。
"""

from pdf_entries import NAMED_FROM_HEADING
from pdf_marks import Mark, bold_name, heading_name
from reviewed_terms import NAMED_FROM_REVIEW

CLASS_NAMES = frozenset({
    "Barbarian", "Bard", "Cleric", "Druid", "Fighter", "Monk",
    "Paladin", "Ranger", "Rogue", "Sorcerer", "Warlock", "Wizard",
})
SUBCLASS_NAMES = frozenset({
    "Path of the Berserker", "College of Lore", "Life Domain",
    "Circle of the Land", "Champion", "Warrior of the Open Hand",
    "Oath of Devotion", "Hunter", "Thief", "Draconic Sorcery",
    "Fiend Patron", "Evoker",
})
# 5.1 正文用的是当时的标题，编号要和已经核对过的 5.2 英文名一致。
_SUBCLASS_FROM_51 = {
    "Way of the Open Hand": "Warrior of the Open Hand",
    "Draconic Bloodline": "Draconic Sorcery",
    "The Fiend": "Fiend Patron",
    "School of Evocation": "Evoker",
}
BACKGROUND_NAMES = frozenset({"Acolyte", "Criminal", "Sage", "Soldier"})
SPECIES_NAMES = frozenset({
    "Dragonborn", "Dwarf", "Elf", "Gnome", "Goliath",
    "Halfling", "Human", "Orc", "Tiefling",
})
FEAT_NAMES = frozenset({
    "Parts of a Feat", "Alert", "Magic Initiate", "Savage Attacker", "Skilled",
    "Ability Score Improvement", "Grappler", "Archery", "Defense",
    "Great Weapon Fighting", "Two-Weapon Fighting",
    "Boon of Combat Prowess", "Boon of Dimensional Travel", "Boon of Fate",
    "Boon of Irresistible Offense", "Boon of Spell Recall",
    "Boon of the Night Spirit", "Boon of Truesight",
})
_CLASS_FEATURES = "Class Features"
_ORIGINS = "# Character Origins"
_BEYOND_FIRST = "## Beyond 1st Level"


def player_marks(lines: list[str], version_token: str) -> list[Mark]:
    """5.2 看 Core Traits 和物种专长。5.1 看 Class Features。"""
    if version_token == "srd52":
        return _marks_52(lines)
    if version_token == "srd51":
        return _marks_51(lines)
    return []


def _marks_52(lines: list[str]) -> list[Mark]:
    limit = _line_index(lines, _ORIGINS)
    if limit is None:
        limit = len(lines)
    marks = _classes_52(lines, limit)
    marks.extend(_named_in_section(
        lines, "## Background Descriptions", {"Character Species"},
        BACKGROUND_NAMES, "background",
    ))
    marks.extend(_species_52(lines))
    marks.extend(_named_in_section(
        lines, "## Feats", set(), FEAT_NAMES, "feat", stop_at_level_one=True,
    ))
    return marks


def _marks_51(lines: list[str]) -> list[Mark]:
    beyond = _line_index(lines, _BEYOND_FIRST)
    limit = beyond if beyond is not None else len(lines)
    marks = _classes_51(lines, limit)
    marks.extend(_named_in_section(
        lines, "## Backgrounds", {"Feats"},
        BACKGROUND_NAMES, "background",
    ))
    marks.extend(_named_in_section(
        lines, "## Feats", {"Using Ability Scores"}, FEAT_NAMES, "feat",
    ))
    return marks


def _classes_52(lines: list[str], limit: int) -> list[Mark]:
    marks: list[Mark] = []
    for index in range(limit):
        parsed = heading_name(lines[index])
        if parsed is None:
            continue
        mark = _class_52(lines, index, parsed[1]) or _subclass_52(lines, index, parsed[1])
        if mark is not None:
            marks.append(mark)
    return marks


def _class_52(lines: list[str], index: int, name: str) -> Mark | None:
    """职业表有时把 Core Traits 写在表头里，有时直接从 Primary Ability 开始。"""
    if name not in CLASS_NAMES:
        return None
    window = "\n".join(lines[index + 1:index + 4])
    if f"Core {name} Traits" in window or "Primary Ability" in window:
        return Mark(index, "class", name)
    return None


def _subclass_52(lines: list[str], index: int, name: str) -> Mark | None:
    """5.2 有的子职业名跟在 Subclass: 后面，吟游诗人则写在下一行。"""
    if "Subclass:" not in name:
        return None
    subclass = name.split("Subclass:", 1)[1].strip() or _next_heading_name(lines, index)
    if subclass not in SUBCLASS_NAMES:
        return None
    return Mark(index, "subclass", subclass)


def _classes_51(lines: list[str], limit: int) -> list[Mark]:
    marks: list[Mark] = []
    for index in range(limit):
        parsed = heading_name(lines[index])
        if parsed is None or parsed[0] != 2:
            continue
        name = parsed[1]
        if name in CLASS_NAMES and _next_heading_name(lines, index) == _CLASS_FEATURES:
            marks.append(Mark(index, "class", name))
            continue
        canonical, status = _subclass_51(name)
        if canonical:
            marks.append(Mark(index, "subclass", canonical, status))
    return marks


def _subclass_51(name: str) -> tuple[str, str]:
    renamed = _SUBCLASS_FROM_51.get(name, "")
    if renamed:
        return renamed, NAMED_FROM_REVIEW
    if name in SUBCLASS_NAMES:
        return name, NAMED_FROM_HEADING
    return "", ""


def _species_52(lines: list[str]) -> list[Mark]:
    start = _line_index(lines, "## Character Species")
    end = _line_index(lines, "## Feats")
    if start is None or end is None or end <= start:
        return []
    marks: list[Mark] = []
    for index in range(start + 1, end):
        name = bold_name(lines[index]) or _heading_text(lines[index])
        if name in SPECIES_NAMES and "Creature Type" in "\n".join(lines[index:index + 4]):
            marks.append(Mark(index, "species", name))
    return marks


def _named_in_section(
    lines: list[str],
    start_text: str,
    stop_names: set[str],
    names: frozenset[str],
    entry_type: str,
    stop_at_level_one: bool = False,
) -> list[Mark]:
    start = _line_index(lines, start_text)
    if start is None:
        return []
    end = _section_end(lines, start, stop_names, stop_at_level_one)
    marks: list[Mark] = []
    for index in range(start + 1, end):
        name = bold_name(lines[index]) or _heading_text(lines[index])
        if name in names:
            marks.append(Mark(index, entry_type, name))
    return marks


def _section_end(
    lines: list[str],
    start: int,
    stop_names: set[str],
    stop_at_level_one: bool,
) -> int:
    for index in range(start + 1, len(lines)):
        parsed = heading_name(lines[index])
        if parsed is None:
            continue
        level, name = parsed
        if name in stop_names or (stop_at_level_one and level == 1):
            return index
    return len(lines)


def _next_heading_name(lines: list[str], index: int) -> str:
    for line in lines[index + 1:index + 4]:
        parsed = heading_name(line)
        if parsed:
            return parsed[1]
        if line.strip():
            return ""
    return ""


def _heading_text(line: str) -> str:
    parsed = heading_name(line)
    if parsed is None or parsed[0] != 2:
        return ""
    return parsed[1]


def _line_index(lines: list[str], text: str) -> int | None:
    for index, line in enumerate(lines):
        if line.strip() == text:
            return index
    return None
