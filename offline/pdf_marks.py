"""英文 PDF 的标题切点。空类型的切点只用来截断上一条，本身不入库。"""

from dataclasses import dataclass

from html_entries import CatalogEntry
from pdf_entries import NAMED_FROM_HEADING, english_entry

_CONDITION_TAG = " [Condition]"
_SPELL_LIST_SUFFIX = " Spell List"
_CHAPTER_HEADINGS = frozenset({
    "Character Creation",
    "Classes",
    "Spell Descriptions",
    "Using Ability Scores",
    "Your Spellbook",
    "Character Species",
    "Feats",
    "Background Descriptions",
    "Spells",
})


@dataclass(frozen=True)
class Mark:
    """一个标题在正文中的位置。entry_type 为空时只作边界。"""

    index: int
    entry_type: str
    name: str
    align_status: str = NAMED_FROM_HEADING


def heading_name(line: str) -> tuple[int, str] | None:
    """返回标题级别和名字。不是标题时返回空。"""
    stripped = line.strip()
    if stripped.startswith("## "):
        return 2, stripped[3:].strip()
    if stripped.startswith("# "):
        return 1, stripped[2:].strip()
    return None


def bold_name(line: str) -> str:
    """整行只有一对加粗时，返回里面的名字。"""
    stripped = line.strip()
    if stripped.startswith("**") and stripped.endswith("**") and stripped.count("**") == 2:
        return stripped[2:-2].strip()
    return ""


def next_content(lines: list[str], index: int) -> str:
    """标题后面第一条有字的行。"""
    for line in lines[index + 1:index + 6]:
        if line.strip():
            return line.strip()
    return ""


def merge_marks(marks: list[Mark]) -> list[Mark]:
    """同一行优先保留真正的条目，边界只在没有条目时留下。"""
    by_index: dict[int, Mark] = {}
    for mark in marks:
        current = by_index.get(mark.index)
        if current is None or (not current.entry_type and mark.entry_type):
            by_index[mark.index] = mark
    return [by_index[index] for index in sorted(by_index)]


def entries_from_marks(
    lines: list[str],
    marks: list[Mark],
    *,
    version_token: str,
    rule_version: str,
    source_id: str,
    source_pin: str,
    pdf_name: str,
) -> list[CatalogEntry]:
    """按切点顺序取正文。下一条切点，不论是否入库，都结束上一条。"""
    ordered = merge_marks(marks)
    entries: list[CatalogEntry] = []
    for position, mark in enumerate(ordered):
        if not mark.entry_type:
            continue
        end = ordered[position + 1].index if position + 1 < len(ordered) else len(lines)
        body = "\n".join(lines[mark.index:end]).strip()
        if not body:
            continue
        entries.append(english_entry(
            version_token=version_token,
            rule_version=rule_version,
            source_id=source_id,
            source_pin=source_pin,
            entry_type=mark.entry_type,
            english_name=mark.name,
            pdf_name=pdf_name,
            line_number=mark.index + 1,
            text=body,
            align_status=mark.align_status,
        ))
    return entries


def structural_boundaries(lines: list[str]) -> list[Mark]:
    """章界、法术表和状态标签。这些标题不属于职业或术语条目。"""
    marks = [_boundary(index) for index, line in enumerate(lines) if _is_boundary(line)]
    marks.extend(_magic_item_boundaries(lines))
    coins = _equipment_chapter(lines)
    if coins is not None:
        marks.append(_boundary(coins))
    return marks


def _is_boundary(line: str) -> bool:
    parsed = heading_name(line)
    if parsed is None:
        return False
    level, name = parsed
    if level == 1:
        return True
    if name in _CHAPTER_HEADINGS or name.endswith(_SPELL_LIST_SUFFIX):
        return True
    if name.startswith("Monsters") or name.startswith("Magic Items A"):
        return True
    return name.endswith(_CONDITION_TAG)


def _magic_item_boundaries(lines: list[str]) -> list[Mark]:
    """物品详述从 A 开头另切。同名的 Magic Items 标题只截断，真正的条目在规则里选。"""
    marks = [
        _boundary(index) for index, line in enumerate(lines)
        if line.strip() == "## Magic Items"
    ]
    az_index = _first_startswith(lines, "## Magic Items A")
    if az_index is not None:
        marks.append(_boundary(az_index))
    return marks


def _equipment_chapter(lines: list[str]) -> int | None:
    """5.1 背景讲完后，用钱币兑换开头的 Equipment 才是装备章。"""
    for index, line in enumerate(lines):
        if line.strip() != "## Equipment":
            continue
        # 背景自己的 Equipment 小节也提到 coin。装备章才用面额开头。
        if "denomination" in next_content(lines, index).lower():
            return index
    return None


def _first_startswith(lines: list[str], prefix: str) -> int | None:
    for index, line in enumerate(lines):
        if line.strip().startswith(prefix):
            return index
    return None


def _boundary(index: int) -> Mark:
    return Mark(index, "", "")
