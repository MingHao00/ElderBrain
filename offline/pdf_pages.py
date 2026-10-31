"""给英文条目补上 PDF 页码。结构化结果按页存放，页码从 1 计。"""

import re
from dataclasses import replace

from html_entries import CatalogEntry
from pdf_spells import REPAIRED_HEADING, spell_header_pattern
from pdf_text import normalize_pdf_text

_MARKDOWN = re.compile(r"[*#]")


def page_texts(structured: dict[str, object]) -> list[tuple[int, str]]:
    """每页整理后的纯文本，页码按文件顺序从 1 开始。"""
    pages = structured.get("pages")
    if not isinstance(pages, list):
        return []
    output: list[tuple[int, str]] = []
    for index, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        blocks = page.get("blocks")
        if not isinstance(blocks, list):
            continue
        raw = "\n".join(_block_text(block) for block in blocks)
        output.append((index + 1, normalize_pdf_text(raw)))
    return output


def locate_entries(
    entries: list[CatalogEntry],
    pages: list[tuple[int, str]],
    pdf_name: str,
) -> list[CatalogEntry]:
    """用条目名加环阶找到起始页。找不到就保留原来的行号。"""
    located: list[CatalogEntry] = []
    for entry in entries:
        page = _first_page(pages, entry)
        if page is None:
            located.append(entry)
            continue
        located.append(replace(entry, location=f"{pdf_name}#p{page}"))
    return located


def _block_text(block: object) -> str:
    if not isinstance(block, dict):
        return ""
    content = block.get("content")
    return content if isinstance(content, str) else ""


def _first_page(pages: list[tuple[int, str]], entry: CatalogEntry) -> int | None:
    # 行首才算标题，避免 Fireball 命中 Delayed Blast Fireball。
    # 补过的标题在页上仍是缺字的那一行。
    if entry.align_status == REPAIRED_HEADING:
        heading = _MARKDOWN.sub("", entry.text.splitlines()[0]).strip()
        pattern = re.compile(rf"(?:^|\n){re.escape(heading)}(?:\n|$)")
    else:
        pattern = spell_header_pattern(entry.title_en)
    for number, text in pages:
        plain = _MARKDOWN.sub("", text)
        if pattern.search(plain):
            return number
    return None
