"""从中文 5.1 Markdown 切出一文件一条的法术和怪物。合订目录不读。"""

import re
from pathlib import Path

from html_entries import CatalogEntry
from ids import RULE_VERSION_51, VERSION_TOKEN_51, concept_key, entry_id
from monster_names import NAMED_FROM_FILENAME, monster_file_identity
from reviewed_terms import canonical_reviewed_heading

SOURCE_ID_51_ZH = "src-srd51-zh"
SOURCE_PIN_51_ZH = "9b711c46d93dbf1d70a9bdf72703e0ecafd50ca8"
SPELL_DIR = "Spells"
MONSTER_DIR = "Monsters"
ALT_DIRS = frozenset({"Monsters (Alt)", "Spells (Alt)"})
SKIP_FILE_NAMES = frozenset({
    "index.md", "TERMS.md", "NOTICE.md", "Legal.md", "Changelog.md",
})
WHOLE_FILE_TYPES = {
    "Treasure": "equipment",
    "Equipment": "equipment",
    "Gameplay": "rule",
    "Gamemastering": "rule",
    "Characterizations": "rule",
}
SECTION_FILES = {
    ("Gamemastering", "Conditions.md"): "condition",
    ("Characterizations", "Feats.md"): "feat",
    ("Characterizations", "Backgrounds.md"): "background",
}
_HEADING = re.compile(r"^(#{1,6}) +(.+)$", re.MULTILINE)


def is_list_filename(name: str) -> bool:
    """以 # 开头的文件是法术列表或怪物总表，不嵌入。"""
    return name.startswith("#")


def first_heading(text: str) -> str:
    matched = _HEADING.search(text)
    if not matched:
        return ""
    return matched.group(2).strip()


def entry_from_markdown_file(
    text: str,
    *,
    relative_path: str,
    entry_type: str,
    english_name: str,
) -> CatalogEntry | None:
    """文件名是英文名。正文整份保留，短条目稍后整条成一块。"""
    title_zh = first_heading(text)
    body = text.strip()
    if not english_name or not body:
        return None
    entry_type, english_name, align_status = _identity(entry_type, english_name)
    english_name, align_status = canonical_reviewed_heading(
        entry_type, english_name, align_status,
    )
    anchor = title_zh or english_name
    return CatalogEntry(
        entry_id=entry_id(VERSION_TOKEN_51, entry_type, english_name),
        concept_key=concept_key(entry_type, english_name),
        rule_version=RULE_VERSION_51,
        source_id=SOURCE_ID_51_ZH,
        source_pin=SOURCE_PIN_51_ZH,
        lang="zh",
        entry_type=entry_type,
        title_en=english_name,
        title_zh=title_zh,
        location=f"{relative_path}#{anchor}",
        text=body,
        align_status=align_status,
    )


def _identity(entry_type: str, english_name: str) -> tuple[str, str, str]:
    if entry_type != "monster":
        return entry_type, english_name, NAMED_FROM_FILENAME
    return monster_file_identity(english_name)


def section_kind(relative_path: str) -> str:
    """一文件多条时返回切分种类。整文件一条的路径返回空字符串。"""
    parts = Path(relative_path).parts
    if len(parts) < 2:
        return ""
    parent, name = parts[-2], parts[-1]
    if parent == "Classes" and name.endswith(".md") and name not in SKIP_FILE_NAMES:
        return "class"
    return SECTION_FILES.get((parent, name), "")


def folder_entry_type(relative_path: str) -> str:
    """整文件一条的类型。需要按标题拆开的文件返回空字符串。"""
    parts = Path(relative_path).parts
    if any(part in ALT_DIRS for part in parts):
        return ""
    name = parts[-1]
    if name in SKIP_FILE_NAMES or is_list_filename(name) or section_kind(relative_path):
        return ""
    parent = parts[-2] if len(parts) >= 2 else ""
    if parent == SPELL_DIR and name.endswith(".md"):
        return "spell"
    if parent == MONSTER_DIR and name.endswith(".md"):
        return "monster"
    if name.endswith(".md"):
        return WHOLE_FILE_TYPES.get(parent, "")
    return ""
