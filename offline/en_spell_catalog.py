"""把整理后的英文 SRD 写成条目清单。法术补起始页，其余先记行号。"""

import json
from dataclasses import asdict
from pathlib import Path

from html_entries import CatalogEntry
from ids import RULE_VERSION_51, RULE_VERSION_52, VERSION_TOKEN_51, VERSION_TOKEN_52
from pdf_conditions import condition_entries
from pdf_items import item_entries
from pdf_monsters import monster_entries
from pdf_monsters51 import monster_entries as monster_entries_51
from pdf_rules51 import rule_entries as rule_entries_51
from pdf_pages import locate_entries, page_texts
from pdf_spells import (
    GARBLED_TITLE,
    REPAIRED_HEADING,
    SOURCE_ID_51_EN,
    SOURCE_ID_521_EN,
    SOURCE_PIN_51_EN,
    SOURCE_PIN_521_EN,
    spell_entries,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PARSE_DIR = REPO_ROOT / "parse"
OUTPUT_DIR = PARSE_DIR / "catalog"


def catalog_from_parse(
    stem: str,
    *,
    version_token: str,
    rule_version: str,
    source_id: str,
    source_pin: str,
    pdf_name: str,
) -> tuple[list[dict[str, str]], list[str], list[str], list[str]]:
    """读取已整理正文。结构化结果缺失时位置保留行号。"""
    text = (PARSE_DIR / f"{stem}.clean.md").read_text(encoding="utf-8")
    fields = {
        "version_token": version_token,
        "rule_version": rule_version,
        "source_id": source_id,
        "source_pin": source_pin,
        "pdf_name": pdf_name,
    }
    spells = spell_entries(text, **fields)
    others = (
        item_entries(text, **fields)
        + monster_entries(text, **fields)
        + monster_entries_51(text, **fields)
        + rule_entries_51(text, **fields)
        + condition_entries(text, **fields)
    )
    structured_path = PARSE_DIR / f"{stem}.structured.json"
    if structured_path.exists():
        structured = json.loads(structured_path.read_text(encoding="utf-8"))
        spells = locate_entries(spells, page_texts(structured), pdf_name)
    garbled = [item.title_en for item in spells if item.align_status == GARBLED_TITLE]
    repaired = [item.title_en for item in spells if item.align_status == REPAIRED_HEADING]
    kept = [item for item in spells if item.align_status != GARBLED_TITLE] + others
    rows = [asdict(item) for item in _dedupe(kept)]
    missing_pages = [
        row["entry_id"] for row in rows
        if row["entry_type"] == "spell" and "#p" not in row["location"]
    ]
    return rows, missing_pages, garbled, repaired


def _dedupe(entries: list[CatalogEntry]) -> list[CatalogEntry]:
    seen: set[str] = set()
    kept = []
    for entry in entries:
        if entry.entry_id in seen:
            continue
        seen.add(entry.entry_id)
        kept.append(entry)
    return kept


def _write_jsonl(path: Path, rows: list[dict[str, str]]) -> None:
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    jobs = (
        (
            "src-srd51-en",
            VERSION_TOKEN_51,
            RULE_VERSION_51,
            SOURCE_ID_51_EN,
            SOURCE_PIN_51_EN,
            "SRD_CC_v5.1.pdf",
        ),
        (
            "src-srd521-en",
            VERSION_TOKEN_52,
            RULE_VERSION_52,
            SOURCE_ID_521_EN,
            SOURCE_PIN_521_EN,
            "SRD_CC_v5.2.1.pdf",
        ),
    )
    for stem, version_token, rule_version, source_id, source_pin, pdf_name in jobs:
        if not (PARSE_DIR / f"{stem}.clean.md").exists():
            print(f"skip {stem}", flush=True)
            continue
        rows, missing_pages, garbled, repaired = catalog_from_parse(
            stem,
            version_token=version_token,
            rule_version=rule_version,
            source_id=source_id,
            source_pin=source_pin,
            pdf_name=pdf_name,
        )
        _write_jsonl(OUTPUT_DIR / f"{stem}.jsonl", rows)
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["entry_type"]] = counts.get(row["entry_type"], 0) + 1
        print(
            f"{stem} {len(rows)} {counts} missing_pages={len(missing_pages)} "
            f"garbled={len(garbled)} repaired={len(repaired)}",
            flush=True,
        )
        if garbled:
            print("garbled: " + "; ".join(garbled), flush=True)
        if missing_pages:
            print(" ".join(missing_pages[:20]), flush=True)


if __name__ == "__main__":
    main()
