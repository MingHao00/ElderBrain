"""选出本轮要送给生成模型的中文 5.1 日常题。"""

import re
from dataclasses import dataclass
from pathlib import Path

from scope import refusal_for

LANG_ZH = "中"
VERSION_51 = "5.1"
SPLIT_DAILY = "日常"
SKIP_HEADINGS = ("版本对比", "版本意图", "后续分期")
ROLE_MODEL = "model"
ROLE_INTERCEPT = "intercept"
TYPE_ANSWER = "answer"
TYPE_REFUSAL = "refusal"
INTERCEPT_CATEGORIES = frozenset({"R6", "R8"})
REFUSAL_CATEGORIES = frozenset({"R1", "R3", "R5"})
PARTIAL_CATEGORY = "R2"
DM_CATEGORY = "R4"
CLARIFY_CATEGORY = "R7"
NO_EVIDENCE = "无"
TABLE_ROW = re.compile(r"^\| ((?:L|M|R)\d+) \|")


@dataclass(frozen=True)
class GenerationQuestion:
    """一道生成实验题。拦截题不送给模型。"""

    question_id: str
    question: str
    role: str
    expected_type: str
    expected_category: str | None
    require_dm: bool
    require_clarify: bool


def load_generation_questions(acceptance: Path) -> list[GenerationQuestion]:
    """只留中文 5.1 的日常题。版本对比、版本意图和放行题不进入本轮。"""
    questions: list[GenerationQuestion] = []
    for row in _table_rows(acceptance):
        built = _question_from_row(row)
        if built is not None:
            questions.append(built)
    return questions


def _question_from_row(row: dict[str, str]) -> GenerationQuestion | None:
    if row["语言"] != LANG_ZH or row["集合"] != SPLIT_DAILY:
        return None
    if "版本" in row and row["版本"] != VERSION_51:
        return None
    category = _category(row.get("类别", ""))
    if category in INTERCEPT_CATEGORIES or refusal_for(row["问题"]) is not None:
        return _intercept(row["编号"], row["问题"])
    if category in REFUSAL_CATEGORIES:
        return _model(row["编号"], row["问题"], TYPE_REFUSAL, category, False, False)
    if category == PARTIAL_CATEGORY and row.get("标准证据") == NO_EVIDENCE:
        return _model(row["编号"], row["问题"], TYPE_REFUSAL, category, False, False)
    # 有原文的 R2 是部分回答；R7 是反问。二者都不是空拒答。
    require_dm = category == DM_CATEGORY
    require_clarify = category == CLARIFY_CATEGORY
    return _model(row["编号"], row["问题"], TYPE_ANSWER, None, require_dm, require_clarify)


def _model(
    question_id: str,
    question: str,
    expected_type: str,
    expected_category: str | None,
    require_dm: bool,
    require_clarify: bool,
) -> GenerationQuestion:
    return GenerationQuestion(
        question_id,
        question,
        ROLE_MODEL,
        expected_type,
        expected_category,
        require_dm,
        require_clarify,
    )


def _intercept(question_id: str, question: str) -> GenerationQuestion:
    return GenerationQuestion(
        question_id,
        question,
        ROLE_INTERCEPT,
        ROLE_INTERCEPT,
        None,
        False,
        False,
    )


def _category(cell: str) -> str:
    token = cell.split(" ", 1)[0].strip()
    return token if token.startswith("R") else ""


def _table_rows(acceptance: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    headers: list[str] = []
    skipping = False
    for line in acceptance.read_text(encoding="utf-8").splitlines():
        if line.startswith("### "):
            skipping = any(name in line for name in SKIP_HEADINGS)
            headers = []
            continue
        if skipping or not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not headers and cells and cells[0] == "编号":
            headers = cells
            continue
        if headers and TABLE_ROW.match(line):
            rows.append(dict(zip(headers, cells, strict=True)))
    return rows
