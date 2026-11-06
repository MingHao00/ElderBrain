"""把文字层表格里粘在一起的词，按 PDF 原文的空格还原。

MinerU 认表格时会丢掉单元格里的空格，还会把好几行骰程并进一格。
PDF 文字层本身仍有空格，所以只在那里能唯一对上时才替换。
"""

import re
from pathlib import Path

_GLUED = re.compile(r"[A-Za-z]{8,}")
_RESTORE = re.compile(r"[A-Za-z]{12,}")
_RANGE = re.compile(r"\d{2}[-–—]\d{2}")
_MIN_TOKEN = 8
_LOOK_BACK = 80
_EFFECT_LIMIT = 500
_SPILL_CHARS = 24
_RANGE_LINE = re.compile(r"\n\s*\d{2}[-–—]\d{2}\b")


def load_pdf_plain(pdf_path: Path) -> str:
    """读出 PDF 文字层，供表格对照。不再跑一遍版面解析。"""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(pdf_path))
    pages = [
        document[index].get_textpage().get_text_bounded()
        for index in range(len(document))
    ]
    return "\n".join(pages)


def repair_glued_tables(text: str, plain: str) -> str:
    """还原粘连的词，并把并在一起的骰程拆回一行一条。"""
    compact = _compact(plain)
    output: list[str] = []
    for line in text.splitlines():
        rows = _range_rows(line, plain)
        output.extend(_restore_row(row, plain, compact) for row in rows)
    return "\n".join(_drop_spilled_effect(output))


def _drop_spilled_effect(lines: list[str]) -> list[str]:
    """表格修好后，原来溢到下一行的同一句不再留着。"""
    output: list[str] = []
    for line in lines:
        if output:
            line = _strip_spill(output[-1], line)
            if not line:
                continue
        output.append(line)
    return output


def _strip_spill(previous: str, line: str) -> str:
    cells = _table_cells(previous)
    if len(cells) != 2:
        return line
    effect = cells[1]
    stripped = line.strip()
    size = min(len(effect), len(stripped))
    while size >= _SPILL_CHARS:
        if effect.endswith(stripped[:size]):
            return stripped[size:].strip()
        size -= 1
    return line


def _range_rows(line: str, plain: str) -> list[str]:
    split = _split_merged_row(line, plain)
    if split is not None:
        return split
    single = _single_glued_range(line, plain)
    if single is None:
        return [line]
    return [single]


def _single_glued_range(line: str, plain: str) -> str | None:
    """一行只有一个骰程、效果却粘在一起时，用 PDF 里该骰程的原文换掉。"""
    cells = _table_cells(line)
    if len(cells) != 2 or _RANGE.fullmatch(cells[0]) is None:
        return None
    if not _looks_glued(cells[1]) or _label_count(plain, cells[0]) != 1:
        return None
    effects = _effects_in_window([cells[0]], plain)
    if len(effects) != 1:
        return None
    return f"| {cells[0]} | {effects[0]} |"


def _looks_glued(text: str) -> bool:
    if re.search(r"[a-z][A-Z]|[a-z]\.[A-Z]|[A-Za-z]\d|\d[A-Za-z]", text):
        return True
    return _RESTORE.search(text) is not None


def _label_count(plain: str, label: str) -> int:
    return len(_label_pattern(label).findall(plain))


def _split_merged_row(line: str, plain: str) -> list[str] | None:
    cells = _table_cells(line)
    if len(cells) != 2:
        return None
    labels = _RANGE.findall(cells[0])
    if len(labels) < 2:
        return None
    tokens = _GLUED.findall(cells[1])
    if not tokens:
        return None
    plain_at = _unique_plain_index(plain, tokens[0].lower())
    if plain_at is None:
        return None
    window = _window_at_first_label(plain, plain_at, labels[0])
    if window is None:
        return None
    effects = _effects_in_window(labels, window)
    if len(effects) != len(labels):
        return None
    return [f"| {label} | {effect} |" for label, effect in zip(labels, effects)]


def _window_at_first_label(plain: str, plain_at: int, label: str) -> str | None:
    """骰程在效果前面。取锚点之前最近的那一处，避开更早的同名骰程。"""
    head_start = max(0, plain_at - _LOOK_BACK)
    found = list(_label_pattern(label).finditer(plain, head_start, plain_at + len(label) + 8))
    if not found:
        return None
    return plain[found[-1].start():]


def _effects_in_window(labels: list[str], window: str) -> list[str]:
    cursor = 0
    effects: list[str] = []
    for index, label in enumerate(labels):
        match = _label_pattern(label).search(window, cursor)
        if match is None:
            return []
        begin = match.end()
        end, cursor = _effect_end(labels, window, begin, index)
        if end is None:
            return []
        effect = re.sub(r"\s+", " ", window[begin:end]).strip(" \n|")
        if not effect:
            return []
        effects.append(effect)
    return effects


def _effect_end(
    labels: list[str],
    window: str,
    begin: int,
    index: int,
) -> tuple[int | None, int]:
    if index + 1 < len(labels):
        nxt = _label_pattern(labels[index + 1]).search(window, begin)
        if nxt is None:
            return None, begin
        return nxt.start(), nxt.start()
    limit = min(len(window), begin + _EFFECT_LIMIT)
    nxt = _RANGE_LINE.search(window, begin, limit)
    if nxt is None:
        return limit, limit
    return nxt.start(), nxt.start()


def _restore_row(line: str, plain: str, compact: str) -> str:
    # 正文里的长词可能只是在 PDF 里换了行。粘连发生在表格单元格。
    if "|" not in line:
        return line
    return _restore_line(line, plain, compact)


def _restore_line(line: str, plain: str, compact: str) -> str:
    def replace(match: re.Match[str]) -> str:
        spaced = _unique_spaced(match.group(0), plain, compact)
        if spaced is None:
            return match.group(0)
        return spaced

    return _RESTORE.sub(replace, line)


def _unique_spaced(token: str, plain: str, compact: str) -> str | None:
    folded = token.lower()
    if len(folded) < _MIN_TOKEN:
        return None
    start = compact.find(folded)
    if start < 0 or compact.find(folded, start + 1) >= 0:
        return None
    chunk = _plain_slice(plain, start, len(folded))
    collapsed = re.sub(r"\s+", " ", chunk).strip()
    if " " not in collapsed:
        return None
    if _compact(collapsed) != folded:
        return None
    return collapsed


def _unique_plain_index(plain: str, anchor: str) -> int | None:
    compact = _compact(plain)
    start = compact.find(anchor)
    if start < 0 or compact.find(anchor, start + 1) >= 0:
        return None
    return _plain_index(plain, start)


def _plain_index(plain: str, compact_at: int) -> int:
    seen = 0
    for index, char in enumerate(plain):
        if char.isspace():
            continue
        if seen == compact_at:
            return index
        seen += 1
    return len(plain)


def _plain_slice(plain: str, compact_at: int, length: int) -> str:
    start = _plain_index(plain, compact_at)
    seen = 0
    end = start
    for index in range(start, len(plain)):
        if plain[index].isspace():
            continue
        seen += 1
        end = index + 1
        if seen == length:
            break
    return plain[start:end]


def _table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    return [part.strip() for part in stripped.strip("|").split("|")]


def _compact(text: str) -> str:
    return "".join(char.lower() for char in text if not char.isspace())


def _label_pattern(label: str) -> re.Pattern[str]:
    left, right = re.split(r"[-–—]", label, maxsplit=1)
    return re.compile(rf"{left}[-–—]{right}")
