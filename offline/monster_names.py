"""把怪物英文名收成英文 PDF 标题所用的写法。

2026-10-31 确认：5.1 文件名里的分类括号去掉；
枭熊、蠕行之爪和幼龙词序按英文 PDF 编号。
巨乌贼在英文 5.2 正文里没有，不入库。
"""

import re

from reviewed_terms import NAMED_FROM_REVIEW

STRIPPED_CATEGORY = "stripped_category"
NAMED_FROM_FILENAME = "named_from_filename"
# 只去掉译者加在文件名上的分类。深地侏儒的 (Svirfneblin) 是名字的一部分。
MONSTER_CATEGORIES = frozenset({
    "Creature", "NPC", "Chromatic", "Metallic", "Demon", "Devil", "Giant",
    "Angel", "Genie", "Ooze", "Animated Object", "Sphinx", "Ghoul",
    "Lycanthrope", "Dinosaur", "Fungi",
})
MONSTERS_ABSENT_FROM_ENGLISH = frozenset({"Giant Squid"})
_CATEGORY_SUFFIX = re.compile(r" \((?P<category>[^)]+)\)$")
_WYRMLING = re.compile(r"^Wyrmling (?P<color>[A-Z][a-z]+) Dragon$")
_MONSTER_ALIASES = {
    "Owlbears": "Owlbear",
    "Swarm of Crawling Claw": "Swarm of Crawling Claws",
}
# 这两页是说明，不是属性块。值是去掉分类后的英文名。
_RULE_FILES = {
    "Customizing NPCs (NPC)": "Customizing NPCs",
    "Half-Dragon Template": "Half-Dragon Template",
}


def canonical_monster_name(name: str, align_status: str) -> tuple[str, str]:
    """返回规范英文名和对照状态。名字不用改时保留原来的状态。"""
    stripped, status = _strip_category(name, align_status)
    renamed = _MONSTER_ALIASES.get(stripped, "")
    if renamed:
        return renamed, NAMED_FROM_REVIEW
    reordered = _reorder_wyrmling(stripped)
    if reordered != stripped:
        return reordered, NAMED_FROM_REVIEW
    return stripped, status


def monster_file_identity(filename_stem: str) -> tuple[str, str, str]:
    """返回 5.1 怪物文件的类型、英文名和对照状态。说明页改为规则。"""
    ruled = _RULE_FILES.get(filename_stem)
    if ruled is None:
        name, status = canonical_monster_name(filename_stem, NAMED_FROM_FILENAME)
        return "monster", name, status
    status = STRIPPED_CATEGORY if ruled != filename_stem else NAMED_FROM_FILENAME
    return "rule", ruled, status


def _strip_category(name: str, align_status: str) -> tuple[str, str]:
    matched = _CATEGORY_SUFFIX.search(name)
    if not matched or matched.group("category") not in MONSTER_CATEGORIES:
        return name, align_status
    return name[:matched.start()], STRIPPED_CATEGORY


def _reorder_wyrmling(name: str) -> str:
    matched = _WYRMLING.match(name)
    if not matched:
        return name
    return f"{matched.group('color')} Dragon Wyrmling"
