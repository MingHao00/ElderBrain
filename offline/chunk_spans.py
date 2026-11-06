"""按标题、段落、句子找出不超上限的原文区间。

表格整段保留：从格子中间切开后，属性对不上。
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

_H2 = re.compile(r"^## +(.+)$", re.MULTILINE)
_H3 = re.compile(r"^### (?!#)(.+)$", re.MULTILINE)
_BLANK = re.compile(r"\n[ \t]*\n")
_PROTECTED = re.compile(r"\d+\.\d+|\d+d\d+|ft\.", re.IGNORECASE)
_SENTENCE_MARKS = "。！？!?"


@dataclass(frozen=True)
class Span:
    start: int
    end: int


def h2_sections(text: str) -> list[tuple[str, Span]]:
    """二级标题各成一节。标题下没有正文的节丢掉。"""
    matches = list(_H2.finditer(text))
    if not matches:
        return _with_body(text, "", Span(0, len(text)))
    sections: list[tuple[str, Span]] = []
    sections.extend(_with_body(text, "", Span(0, matches[0].start())))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        heading = match.group(1).strip()
        sections.extend(_with_body(text, heading, Span(match.start(), end)))
    return sections


def spans_under_limit(
    text: str,
    span: Span,
    limit: int,
    count_tokens: Callable[[str], int],
) -> list[Span]:
    """超上限时先按三级标题，再按段落，最后按句子。切不开就整段留下。"""
    piece = text[span.start:span.end]
    if not piece.strip():
        return []
    if count_tokens(piece) <= limit or is_table_block(piece):
        return [span]
    by_heading = h3_spans(text, span)
    if len(by_heading) > 1:
        return pack(text, _flatten(text, by_heading, limit, count_tokens), limit, count_tokens)
    by_paragraph = paragraph_spans(text, span)
    if len(by_paragraph) > 1:
        return pack(text, _flatten(text, by_paragraph, limit, count_tokens), limit, count_tokens)
    by_sentence = sentence_spans(text, span)
    if len(by_sentence) > 1:
        return pack(text, _flatten(text, by_sentence, limit, count_tokens), limit, count_tokens)
    return [span]


def has_body(text: str) -> bool:
    """只有标题行的片段没有可检索的规则。"""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return True
    return False


def is_table_block(text: str) -> bool:
    """整张表留在同一块。表头和数值拆开后无法对上。"""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    pipe_lines = [line for line in lines if line.startswith("|")]
    html_rows = [line for line in lines if line.lower().startswith(("<table", "<tr", "<td"))]
    if len(pipe_lines) >= 2 and len(pipe_lines) >= len(lines) - 1:
        return True
    return len(html_rows) >= 2 and len(html_rows) >= len(lines) - 1


def h3_spans(text: str, span: Span) -> list[Span]:
    """三级标题切开。四级标题留在所属的三级标题里。"""
    matches = list(_H3.finditer(text, span.start, span.end))
    if not matches:
        return []
    parts: list[Span] = []
    parts.extend(_nonempty(text, Span(span.start, matches[0].start())))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else span.end
        parts.extend(_nonempty(text, Span(match.start(), end)))
    return parts


def paragraph_spans(text: str, span: Span) -> list[Span]:
    raw = _raw_paragraphs(text, span)
    return _glue_headings(text, raw)


def sentence_spans(text: str, span: Span) -> list[Span]:
    """句号处切开，但不切断 5.1、8d6 和 ft.。"""
    if is_table_block(text[span.start:span.end]):
        return [span]
    protected = [
        (match.start(), match.end())
        for match in _PROTECTED.finditer(text, span.start, span.end)
    ]
    cuts = [span.start]
    for index in range(span.start, span.end):
        if _ends_sentence(text, index, span.end, protected):
            cuts.append(index + 1)
    cuts.append(span.end)
    parts: list[Span] = []
    for start, end in zip(cuts, cuts[1:]):
        parts.extend(_trimmed(text, Span(start, end)))
    return parts


def pack(
    text: str,
    spans: list[Span],
    limit: int,
    count_tokens: Callable[[str], int],
) -> list[Span]:
    """相邻短块能合进上限就合并。不重叠。"""
    if not spans:
        return []
    packed: list[Span] = [spans[0]]
    for span in spans[1:]:
        merged = Span(packed[-1].start, span.end)
        if count_tokens(text[merged.start:merged.end]) <= limit:
            packed[-1] = merged
            continue
        packed.append(span)
    return packed


def _with_body(text: str, heading: str, span: Span) -> list[tuple[str, Span]]:
    if not has_body(text[span.start:span.end]):
        return []
    return [(heading, span)]


def _flatten(
    text: str,
    spans: list[Span],
    limit: int,
    count_tokens: Callable[[str], int],
) -> list[Span]:
    flat: list[Span] = []
    for span in spans:
        flat.extend(spans_under_limit(text, span, limit, count_tokens))
    return flat


def _raw_paragraphs(text: str, span: Span) -> list[Span]:
    parts: list[Span] = []
    position = span.start
    for match in _BLANK.finditer(text, span.start, span.end):
        parts.extend(_nonempty(text, Span(position, match.start())))
        position = match.end()
    parts.extend(_nonempty(text, Span(position, span.end)))
    return parts


def _glue_headings(text: str, spans: list[Span]) -> list[Span]:
    """标题行没有正文时，跟后面的段落留在一起。"""
    glued: list[Span] = []
    index = 0
    while index < len(spans):
        end_index = index
        while end_index + 1 < len(spans) and not has_body(_slice(text, spans[index], spans[end_index])):
            end_index += 1
        glued.append(Span(spans[index].start, spans[end_index].end))
        index = end_index + 1
    return glued


def _slice(text: str, start: Span, end: Span) -> str:
    return text[start.start:end.end]


def _nonempty(text: str, span: Span) -> list[Span]:
    if text[span.start:span.end].strip():
        return [span]
    return []


def _trimmed(text: str, span: Span) -> list[Span]:
    start, end = span.start, span.end
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start >= end:
        return []
    return [Span(start, end)]


def _ends_sentence(text: str, index: int, end: int, protected: list[tuple[int, int]]) -> bool:
    if any(start <= index < stop for start, stop in protected):
        return False
    char = text[index]
    if char in _SENTENCE_MARKS:
        return True
    if char != ".":
        return False
    return index + 1 >= end or text[index + 1].isspace()
