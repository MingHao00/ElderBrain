"""整理 SRD PDF 文字层。版面用制表符和软连字符撑开字距，不是正文。"""

import re

_LINE_BREAK_HYPHEN = "-\u00ad\u2010\u2011"
_SOFT_HYPHEN = "\u00ad"
_UNICODE_HYPHEN = "\u2010"
_NB_HYPHEN = "\u2011"
_APOSTROPHE = "\u2019"
_NBSP = "\u00a0"
_TAB = "\t"
_SPACE_RUN = re.compile(r"[ ]+")
_ABILITY_WORDS = ("STR", "DEX", "CON", "INT", "WIS", "CHA")
_ABILITY_HEADER = "| STR | DEX | CON | INT | WIS | CHA |"
_ABILITY_RULE = "| --- | --- | --- | --- | --- | --- |"
_ABILITY_SCORE = re.compile(r"(\d+)\s*\(\s*([+-]?\d+)\s*\)")
_TABLE_RULE = re.compile(r"^\|\s*-{3,}")


def normalize_pdf_text(raw: str) -> str:
    """把文字层收成普通空格和连字符，保留换行。"""
    text = raw.replace(_LINE_BREAK_HYPHEN, "-")
    text = text.replace(_SOFT_HYPHEN, "")
    text = text.replace(_UNICODE_HYPHEN, "-").replace(_NB_HYPHEN, "-")
    text = text.replace(_APOSTROPHE, "'")
    text = text.replace(_TAB, " ").replace(_NBSP, " ")
    lines = [_SPACE_RUN.sub(" ", line).strip() for line in text.splitlines()]
    kept = [line for line in lines if line]
    return "\n".join(_repair_ability_tables(kept))


def _repair_ability_tables(lines: list[str]) -> list[str]:
    """文字层会丢掉属性表的一根竖线，六项数值仍按从左到右的顺序留着。"""
    output: list[str] = []
    index = 0
    while index < len(lines):
        scores = _ability_scores(lines, index)
        if scores is None:
            output.append(lines[index])
            index += 1
            continue
        found, nxt = scores
        output.append(_ABILITY_HEADER)
        output.append(_ABILITY_RULE)
        output.append(_score_row(found))
        index = nxt
    return output


def _ability_scores(lines: list[str], index: int) -> tuple[list[tuple[str, str]], int] | None:
    if not _is_ability_header(lines[index]):
        return None
    cursor = index + 1
    if cursor < len(lines) and _TABLE_RULE.match(lines[cursor]):
        cursor += 1
    if cursor >= len(lines):
        return None
    found = _ABILITY_SCORE.findall(lines[cursor])
    if len(found) != len(_ABILITY_WORDS):
        return None
    return found, cursor + 1


def _is_ability_header(line: str) -> bool:
    if not line.startswith("|"):
        return False
    return all(re.search(rf"\b{word}\b", line) for word in _ABILITY_WORDS)


def _score_row(scores: list[tuple[str, str]]) -> str:
    cells = " | ".join(f"{score} ({modifier})" for score, modifier in scores)
    return f"| {cells} |"
