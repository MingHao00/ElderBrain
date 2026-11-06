"""5.2 术语汇编。方括号里的家族标签不进入编号，状态已经单独切过。"""

import re

from pdf_entries import NAMED_FROM_HEADING
from pdf_marks import Mark, heading_name

STRIPPED_TAG = "stripped_tag"
_SKIP_HEADINGS = frozenset({"Glossary Conventions", "Rules Definitions"})
_FAMILY_TAG = re.compile(r" \[(Action|Hazard|Attitude|Area of Effect)\]$")
_GLOSSARY = "# Rules Glossary"
_TOOLBOX = "# Gameplay Toolbox"


def glossary_marks(lines: list[str]) -> list[Mark]:
    """汇编开头的通则并进 Rules Glossary。后面每个二级标题是一条术语。"""
    start = _exact(lines, _GLOSSARY)
    end = _exact(lines, _TOOLBOX)
    if start is None or end is None or end <= start:
        return []
    marks = [Mark(start, "rule", "Rules Glossary")]
    for index in range(start + 1, end):
        mark = _glossary_mark(lines[index], index)
        if mark is not None:
            marks.append(mark)
    return marks


def _glossary_mark(line: str, index: int) -> Mark | None:
    parsed = heading_name(line)
    if parsed is None or parsed[0] != 2:
        return None
    name = parsed[1]
    if name.endswith(" [Condition]") or name in _SKIP_HEADINGS:
        return None
    stripped = _FAMILY_TAG.sub("", name)
    status = STRIPPED_TAG if stripped != name else NAMED_FROM_HEADING
    return Mark(index, "glossary", stripped, status)


def _exact(lines: list[str], text: str) -> int | None:
    for index, line in enumerate(lines):
        if line.strip() == text:
            return index
    return None
