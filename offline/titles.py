"""从条目标题里取出中文名和英文名。对齐的第一步只用标题自带的英文名。"""

import re

_PIPE = re.compile(r"^(?P<zh>.+?)\s*[|｜]\s*(?P<en>.+)$")
# 从末尾收回整段英文名。中文可能直接黏在第一个单词上，例如「山丘巨人Hill Giant」。
_EN_TOKEN = r"[A-Za-z0-9'’/+:,.()-]+"
_TRAILING_EN = re.compile(
    rf"^(?P<zh>.*?)(?P<en>[A-Z]{_EN_TOKEN}(?:\s+{_EN_TOKEN})*)$"
)
_SPACE = re.compile(r"\s+")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_QUOTES = re.compile("[“”„«»「」\"]")
_TAG = re.compile(r"<[^>]+>")


def heading_text(raw_heading: str) -> str:
    """去掉标题标签里残留的标记。"""
    return _TAG.sub("", raw_heading).strip()


def split_bilingual_title(raw_heading: str) -> tuple[str, str]:
    """返回 (中文名, 英文名)。没有英文名时英文名为空字符串。"""
    text = _QUOTES.sub("", heading_text(raw_heading))
    text = _SPACE.sub(" ", text).strip(" 。.")
    piped = _PIPE.match(text)
    if piped:
        return piped.group("zh").strip(), piped.group("en").strip()
    trailing = _TRAILING_EN.match(text)
    if not trailing:
        return text, ""
    zh_name = trailing.group("zh").strip(" \t-|｜")
    en_name = trailing.group("en").strip()
    if zh_name and not _CJK.search(zh_name):
        return text, ""
    if not zh_name:
        return "", en_name
    return zh_name, en_name
