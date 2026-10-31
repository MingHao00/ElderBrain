"""把 GBK HTML 收成纯文本。表格单元格保留分隔，避免数值粘在一起。"""

import re
from html.parser import HTMLParser

_BLOCK_TAGS = frozenset({
    "br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote",
})
_SKIP_TAGS = frozenset({"style", "script"})
_SPACE_RUN = re.compile(r"[ \t]+")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if lowered in _BLOCK_TAGS:
            self._parts.append("\n")
        elif lowered == "td":
            self._parts.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        lines = [_SPACE_RUN.sub(" ", line).strip() for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)


def html_to_text(fragment: str) -> str:
    """去掉标签，保留换行。链接只留可见文字。"""
    extractor = _TextExtractor()
    extractor.feed(fragment)
    extractor.close()
    return extractor.text()
