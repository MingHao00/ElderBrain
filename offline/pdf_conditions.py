"""从英文 SRD 切出状态。

5.2 的标题带 [Condition]，正文到下一个标题为止。
5.1 收在 Appendix PH-A 里；力竭等级表不是新状态，留在 Exhaustion 里。
"""

import re

from html_entries import CatalogEntry
from pdf_entries import english_entry

_MARKED = re.compile(r"^## (?P<name>.+?) \[Condition\]$")
_HEADING = re.compile(r"^## (?P<name>.+)$")
_APPENDIX_START = "# Appendix PH-A: Conditions"
_APPENDIX_END = "# Appendix PH-B:"
CONDITION_NAMES = frozenset({
    "Blinded", "Charmed", "Deafened", "Exhaustion", "Frightened",
    "Grappled", "Incapacitated", "Invisible", "Paralyzed", "Petrified",
    "Poisoned", "Prone", "Restrained", "Stunned", "Unconscious",
})


def condition_entries(
    text: str,
    *,
    version_token: str,
    rule_version: str,
    source_id: str,
    source_pin: str,
    pdf_name: str,
) -> list[CatalogEntry]:
    """5.2 用带标记的标题。没有标记时再读 5.1 的附录。"""
    lines = text.splitlines()
    marked = _marked_spans(lines)
    spans = marked if marked else _appendix_spans(lines)
    return [
        _entry(lines, start, end, name, version_token, rule_version,
               source_id, source_pin, pdf_name)
        for start, end, name in spans
    ]


def _marked_spans(lines: list[str]) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        matched = _MARKED.match(line.strip())
        if not matched:
            continue
        spans.append((index, _next_heading(lines, index), matched.group("name").strip()))
    return spans


def _appendix_spans(lines: list[str]) -> list[tuple[int, int, str]]:
    start, region_end = _appendix_bounds(lines)
    if start is None:
        return []
    names = _appendix_names(lines, start, region_end)
    spans: list[tuple[int, int, str]] = []
    for position, (index, name) in enumerate(names):
        end = names[position + 1][0] if position + 1 < len(names) else region_end
        spans.append((index, end, name))
    return spans


def _appendix_bounds(lines: list[str]) -> tuple[int | None, int]:
    start = next((i for i, line in enumerate(lines) if line.strip() == _APPENDIX_START), None)
    if start is None:
        return None, len(lines)
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith(_APPENDIX_END)),
        len(lines),
    )
    return start, end


def _appendix_names(lines: list[str], start: int, end: int) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for index in range(start, end):
        matched = _HEADING.match(lines[index].strip())
        if not matched:
            continue
        name = matched.group("name").strip()
        if name in CONDITION_NAMES:
            found.append((index, name))
    return found


def _next_heading(lines: list[str], start: int) -> int:
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("#"):
            return index
    return len(lines)


def _entry(
    lines: list[str],
    start: int,
    end: int,
    name: str,
    version_token: str,
    rule_version: str,
    source_id: str,
    source_pin: str,
    pdf_name: str,
) -> CatalogEntry:
    return english_entry(
        version_token=version_token,
        rule_version=rule_version,
        source_id=source_id,
        source_pin=source_pin,
        entry_type="condition",
        english_name=name,
        pdf_name=pdf_name,
        line_number=start + 1,
        text="\n".join(lines[start:end]).strip(),
    )
