"""从验收题里抽出计入召回分母的中文 5.1 题，并标上权威 entry_id。"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

EVAL_VERSION = "eval-v0.2"
SOURCE_ID = "src-srd51-zh"
LANG_ZH = "中"
VERSION_51 = "5.1"
SPLIT_DAILY = "日常"
SPLIT_HOLDOUT = "放行"
SKIP_HEADINGS = ("版本对比", "版本意图", "后续分期")
EXCLUDED_MARK = "不进入召回分母"
EVIDENCE_PATH = re.compile(r"`src-srd51-zh`\s*·\s*`([^`]+)`")
TABLE_ROW = re.compile(r"^\| ((?:L|M|R)\d+) \|")
HEADING_LINE = re.compile(r"^#{1,6} +(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class RecallQuestion:
    """一道计入召回的题。放行题不参与挑选模型。"""

    question_id: str
    question: str
    entry_ids: tuple[str, ...]
    locations: tuple[str, ...]
    split: str


def load_recall_questions(
    acceptance: Path,
    catalog: Path,
) -> list[RecallQuestion]:
    """只保留中文 5.1、有中文证据、且没有被排除的 L、M、R。"""
    by_location, by_file = _catalog_index(catalog)
    questions: list[RecallQuestion] = []
    for row in _table_rows(acceptance):
        built = _question_from_row(row, by_location, by_file)
        if built is not None:
            questions.append(built)
    return questions


def _question_from_row(
    row: dict[str, str],
    by_location: dict[str, str],
    by_file: dict[str, list[tuple[str, str]]],
) -> RecallQuestion | None:
    if row["语言"] != LANG_ZH:
        return None
    if "版本" in row and row["版本"] != VERSION_51:
        return None
    evidence = row["标准证据"]
    if EXCLUDED_MARK in evidence:
        return None
    locations = tuple(EVIDENCE_PATH.findall(evidence))
    if not locations:
        return None
    entry_ids = tuple(
        _entry_id_for(location, by_location, by_file) for location in locations
    )
    return RecallQuestion(
        row["编号"],
        row["问题"],
        entry_ids,
        locations,
        row["集合"],
    )


def _entry_id_for(
    location: str,
    by_location: dict[str, str],
    by_file: dict[str, list[tuple[str, str]]],
) -> str:
    found = by_location.get(location)
    if found is not None:
        return found
    file_path, _, anchor = location.partition("#")
    matched = [
        entry_id
        for entry_id, text in by_file.get(file_path, [])
        if anchor and _contains_heading(text, anchor)
    ]
    if len(matched) != 1:
        raise ValueError(f"无法从证据定位唯一条目: {location}")
    return matched[0]


def _contains_heading(text: str, anchor: str) -> bool:
    return any(heading == anchor for heading in HEADING_LINE.findall(text))


def _catalog_index(
    catalog: Path,
) -> tuple[dict[str, str], dict[str, list[tuple[str, str]]]]:
    by_location: dict[str, str] = {}
    by_file: dict[str, list[tuple[str, str]]] = {}
    for row in _read_jsonl(catalog):
        location = str(row["location"])
        entry_id = str(row["entry_id"])
        by_location[location] = entry_id
        file_path = location.split("#", 1)[0]
        by_file.setdefault(file_path, []).append((entry_id, str(row["text"])))
    return by_location, by_file


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


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows
