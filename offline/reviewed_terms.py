"""已经核对过的中英对照，以及源文里写错的英文名。

2026-10-31 确认：5.1 的旧译名对齐到 5.2 的英文名；
耳聋的英文和狂战士道途的英文按标准拼写编号。
"""

NAMED_FROM_REVIEW = "named_from_review"
REPAIRED_HEADING = "repaired_heading"
NAMED_FROM_HEADING = "named_from_heading"

ENGLISH_NAME_REPAIRS = {
    "Defened": "Deafened",
    "Path of the Berserke": "Path of the Berserker",
}

REVIEWED_ALIASES = {
    "feat": {
        "擒抱专家": "Grappler",
    },
    "subclass": {
        "狂战士之道": "Path of the Berserker",
        "博学学院": "College of Lore",
        "斗士": "Champion",
        "开掌之道": "Warrior of the Open Hand",
        "奉献誓言": "Oath of Devotion",
        "龙脉术法": "Draconic Sorcery",
        "邪魔": "Fiend Patron",
        "塑能学派": "Evoker",
    },
    "condition": {
        "被擒抱": "Grappled",
        "受拘束": "Restrained",
        "失去意识": "Unconscious",
    },
}


def reviewed_english_name(entry_type: str, title_zh: str) -> str:
    """返回已核对的英文名。没有这条对照时返回空字符串。"""
    return REVIEWED_ALIASES.get(entry_type, {}).get(title_zh, "")


def repair_source_english(name: str, align_status: str) -> tuple[str, str]:
    """源文英文名写错时换成标准拼写，并标成已修正。"""
    repaired = ENGLISH_NAME_REPAIRS.get(name)
    if not repaired:
        return name, align_status
    if align_status == NAMED_FROM_HEADING:
        return repaired, REPAIRED_HEADING
    return repaired, align_status
