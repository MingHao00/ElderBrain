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


def normalize_pdf_text(raw: str) -> str:
    """把文字层收成普通空格和连字符，保留换行。"""
    text = raw.replace(_LINE_BREAK_HYPHEN, "-")
    text = text.replace(_SOFT_HYPHEN, "")
    text = text.replace(_UNICODE_HYPHEN, "-").replace(_NB_HYPHEN, "-")
    text = text.replace(_APOSTROPHE, "'")
    text = text.replace(_TAB, " ").replace(_NBSP, " ")
    lines = [_SPACE_RUN.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)
