"""从英文 5.1 切出怪物附录里的说明。示例属性块留给怪物条目。"""

import re

from html_entries import CatalogEntry
from pdf_entries import english_entry

_RULE_HEADINGS = {
    "## Half-Dragon Template": "Half-Dragon Template",
    "## Customizing NPCs": "Customizing NPCs",
}
_HEADING = re.compile(r"^#{1,2} ")


def rule_entries(
    text: str,
    *,
    version_token: str,
    rule_version: str,
    source_id: str,
    source_pin: str,
    pdf_name: str,
) -> list[CatalogEntry]:
    """只切半龙模板和自定义 NPC。5.2 没有这两节时返回空列表。"""
    lines = text.splitlines()
    entries: list[CatalogEntry] = []
    for index, line in enumerate(lines):
        name = _RULE_HEADINGS.get(line.strip(), "")
        if not name:
            continue
        end = _rule_end(lines, index)
        body = "\n".join(lines[index:end]).strip()
        if not body:
            continue
        entries.append(english_entry(
            version_token=version_token,
            rule_version=rule_version,
            source_id=source_id,
            source_pin=source_pin,
            entry_type="rule",
            english_name=name,
            pdf_name=pdf_name,
            line_number=index + 1,
            text=body,
        ))
    return entries


def _rule_end(lines: list[str], start: int) -> int:
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if _HEADING.match(stripped) or _bold_name_line(stripped):
            return index
    return len(lines)


def _bold_name_line(line: str) -> bool:
    """整行只有加粗名字时，是下一条示例怪物。"""
    return line.startswith("**") and line.endswith("**")
