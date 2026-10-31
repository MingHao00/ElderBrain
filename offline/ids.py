"""条目编号。中英文共用同一条 entry_id，所以英文名先收成稳定的连字符。"""

import re

_APOSTROPHE = re.compile(r"['’]")
_NON_SLUG = re.compile(r"[^a-z0-9]+")

VERSION_TOKEN_51 = "srd51"
VERSION_TOKEN_52 = "srd52"
RULE_VERSION_51 = "5.1"
RULE_VERSION_52 = "5.2"


def slug(english_name: str) -> str:
    """把英文条目名收成小写连字符，供 entry_id 使用。"""
    lowered = english_name.strip().lower()
    without_apostrophe = _APOSTROPHE.sub("", lowered)
    hyphenated = _NON_SLUG.sub("-", without_apostrophe)
    return hyphenated.strip("-")


def entry_id(version_token: str, entry_type: str, english_name: str) -> str:
    """一个版本里的一条规则。例子：srd51:spell:fireball。"""
    return f"{version_token}:{entry_type}:{slug(english_name)}"


def concept_key(entry_type: str, english_name: str) -> str:
    """跨版本的同名键。例子：spell:fireball。"""
    return f"{entry_type}:{slug(english_name)}"
