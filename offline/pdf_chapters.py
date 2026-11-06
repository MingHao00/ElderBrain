"""英文 PDF 里职业章、术语汇编和其余规则。法术、物品、怪物、状态仍由各自的切分负责。"""

from html_entries import CatalogEntry
from ids import VERSION_TOKEN_51, VERSION_TOKEN_52
from pdf_conditions import CONDITION_NAMES
from pdf_entries import NAMED_FROM_HEADING
from reviewed_terms import NAMED_FROM_REVIEW
from pdf_glossary_marks import glossary_marks
from pdf_marks import Mark, entries_from_marks, heading_name, structural_boundaries
from pdf_player_marks import player_marks
from pdf_rule_titles import (
    EXTRA_RULE_HEADINGS_52,
    RULE_TITLES_51,
    RULE_TITLES_52,
    SPELL_FIELD_NAMES,
)

_LATE_MAGIC = frozenset({"Magic Items", "Armor"})
_APPENDIX_NAMES = (
    ("# Appendix PH-A:", "Conditions"),
    ("# Appendix PH-B:", "Pantheons"),
    ("# Appendix PH-C:", "Planes"),
)


def chapter_entries(
    text: str,
    *,
    version_token: str,
    rule_version: str,
    source_id: str,
    source_pin: str,
    pdf_name: str,
) -> list[CatalogEntry]:
    """切出玩家选项、术语和章节规则。两种版本缺的章节会得到空列表。"""
    lines = text.splitlines()
    marks = structural_boundaries(lines)
    marks.extend(player_marks(lines, version_token))
    marks.extend(_rule_marks(lines, version_token))
    marks.extend(glossary_marks(lines))
    if version_token == VERSION_TOKEN_51:
        marks.extend(_appendix_marks(lines))
        marks.extend(_gameplay_spans_51(lines))
    return entries_from_marks(
        lines, marks,
        version_token=version_token,
        rule_version=rule_version,
        source_id=source_id,
        source_pin=source_pin,
        pdf_name=pdf_name,
    )


def _rule_marks(lines: list[str], version_token: str) -> list[Mark]:
    if version_token == VERSION_TOKEN_52:
        names = RULE_TITLES_52 | EXTRA_RULE_HEADINGS_52
        chooser = _choose_52
    elif version_token == VERSION_TOKEN_51:
        names = RULE_TITLES_51
        chooser = _choose_51
    else:
        return []
    hits = _heading_hits(lines)
    anchors = _anchors(lines)
    marks: list[Mark] = []
    for name in sorted(names):
        index = chooser(name, hits.get(name, []), anchors)
        if index is not None:
            marks.append(Mark(index, "rule", name, NAMED_FROM_HEADING))
    return marks


def _choose_52(name: str, hits: list[int], anchors: "_Anchors") -> int | None:
    """同名标题优先取玩法章。护甲和魔法物品要取城主章，不取装备章里的同名标题。"""
    if name in SPELL_FIELD_NAMES:
        return _between(hits, anchors.spells, anchors.spell_descriptions)
    if name in _LATE_MAGIC:
        return _between(hits, anchors.magic_items, anchors.magic_az)
    early = [hit for hit in hits if hit < anchors.glossary]
    if early:
        return early[0]
    toolbox = _between(hits, anchors.toolbox, anchors.magic_items)
    if toolbox is not None:
        return toolbox
    return _between(hits, anchors.magic_items, anchors.magic_az)


def _choose_51(name: str, hits: list[int], anchors: "_Anchors") -> int | None:
    """种族特征里的 Alignment 和有感知物品的 Abilities 都不是那两节规则。"""
    found = []
    for hit in hits:
        if hit <= anchors.barbarian or hit >= anchors.sentient:
            continue
        if name == "Languages" and _language_inside_background(hit, anchors):
            continue
        found.append(hit)
    if not found:
        return None
    return found[0]


def _language_inside_background(hit: int, anchors: "_Anchors") -> bool:
    """背景说明里的 Languages 是背景的一小节，不是语言规则。"""
    if anchors.backgrounds < 0:
        return False
    return anchors.backgrounds < hit < anchors.acolyte


def _appendix_marks(lines: list[str]) -> list[Mark]:
    """附录按审阅用短名。状态定义仍单独切，这里只留附录开头。"""
    marks: list[Mark] = []
    for prefix, name in _APPENDIX_NAMES:
        index = _startswith_index(lines, prefix)
        if index is None:
            continue
        marks.append(Mark(index, "rule", name, NAMED_FROM_REVIEW))
    start = _startswith_index(lines, "# Appendix PH-A:")
    if start is None:
        return marks
    for index in range(start + 1, len(lines)):
        parsed = heading_name(lines[index])
        if parsed and parsed[1] in CONDITION_NAMES:
            marks.append(Mark(index, "", ""))
            break
    return marks


def _gameplay_spans_51(lines: list[str]) -> list[Mark]:
    """属性、冒险、战斗在英文稿里没有同名章标题。审阅按这些边界收成一条。"""
    ability = _exact_index(lines, "## Using Ability Scores")
    if ability is None:
        return []
    time = _first_after(lines, "## Time", ability)
    order = _first_after(lines, "## The Order of Combat", time)
    casting = _first_after(lines, "## Spellcasting", order)
    marks = [Mark(ability, "rule", "Using Ability Scores")]
    if time < len(lines):
        marks.append(Mark(time, "rule", "Adventuring", NAMED_FROM_REVIEW))
    if order < len(lines):
        marks.append(Mark(order, "rule", "Combat", NAMED_FROM_REVIEW))
    if casting < len(lines):
        marks.append(Mark(casting, "", ""))
    return marks


class _Anchors:
    def __init__(self, lines: list[str]) -> None:
        glossary = _exact_index(lines, "# Rules Glossary")
        self.glossary = glossary if glossary is not None else len(lines)
        toolbox = _exact_index(lines, "# Gameplay Toolbox")
        self.toolbox = toolbox if toolbox is not None else len(lines)
        self.spell_descriptions = _exact_index(lines, "## Spell Descriptions") or len(lines)
        self.spells = _last_exact_before(lines, "## Spells", self.spell_descriptions)
        self.magic_az = _startswith_index(lines, "## Magic Items A") or len(lines)
        self.magic_items = _last_exact_before(lines, "## Magic Items", self.magic_az)
        barbarian = _exact_index(lines, "## Barbarian")
        self.barbarian = barbarian if barbarian is not None else -1
        sentient = _exact_index(lines, "## Sentient Magic Items")
        self.sentient = sentient if sentient is not None else len(lines)
        backgrounds = _exact_index(lines, "## Backgrounds")
        self.backgrounds = backgrounds if backgrounds is not None else -1
        self.acolyte = _first_after(lines, "## Acolyte", self.backgrounds)


def _anchors(lines: list[str]) -> _Anchors:
    return _Anchors(lines)


def _heading_hits(lines: list[str]) -> dict[str, list[int]]:
    hits: dict[str, list[int]] = {}
    for index, line in enumerate(lines):
        parsed = heading_name(line)
        if parsed is None or parsed[0] != 2:
            continue
        hits.setdefault(parsed[1], []).append(index)
    return hits


def _between(hits: list[int], start: int | None, end: int | None) -> int | None:
    if start is None or end is None:
        return None
    found = [hit for hit in hits if start <= hit < end]
    if not found:
        return None
    return found[0]


def _exact_index(lines: list[str], text: str) -> int | None:
    for index, line in enumerate(lines):
        if line.strip() == text:
            return index
    return None


def _last_exact_before(lines: list[str], text: str, limit: int) -> int | None:
    found = [index for index in range(limit) if lines[index].strip() == text]
    if not found:
        return None
    return found[-1]


def _startswith_index(lines: list[str], prefix: str) -> int | None:
    for index, line in enumerate(lines):
        if line.strip().startswith(prefix):
            return index
    return None


def _first_after(lines: list[str], text: str, start: int) -> int:
    for index in range(max(start, 0) + 1, len(lines)):
        if lines[index].strip() == text:
            return index
    return len(lines)
