"""从已经对齐的中文 5.2 条目标题抽出术语表，供 5.1 的中文标题回查。"""

from html_entries import CatalogEntry


def term_index(entries: list[CatalogEntry]) -> dict[str, dict[str, str]]:
    """返回 entry_type → 中文名 → 英文名。同一中文名对应两个英文名时整条丢弃。"""
    seen: dict[tuple[str, str], set[str]] = {}
    for entry in entries:
        if not entry.title_zh or not entry.title_en:
            continue
        key = (entry.entry_type, entry.title_zh)
        seen.setdefault(key, set()).add(entry.title_en)
    index: dict[str, dict[str, str]] = {}
    for (entry_type, title_zh), names in seen.items():
        if len(names) != 1:
            continue
        index.setdefault(entry_type, {})[title_zh] = next(iter(names))
    return index
