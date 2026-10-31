"""写出中文来源的条目清单。英文 PDF 的非法术条目还没切。"""

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from html_entries import (
    MONSTER_BOOK_NAME,
    SPELL_DIR_NAME,
    CatalogEntry,
    entries_from_monster_html,
    entries_from_spell_html,
    is_skipped_html,
    read_gbk_html,
)
from html_files import classify_html, entries_from_classified_html
from markdown_entries import (
    entry_from_markdown_file,
    folder_entry_type,
    section_kind,
)
from markdown_sections import section_entries
from terms import term_index

REPO_ROOT = Path(__file__).resolve().parents[1]
HTML_ROOT = REPO_ROOT / "SRD5.2Chm"
MARKDOWN_ROOT = REPO_ROOT / "DND.SRD.zh-CN" / "docs"
OUTPUT_DIR = REPO_ROOT / "parse" / "catalog"


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def collect_html() -> tuple[list[CatalogEntry], list[str], list[str]]:
    """法术、怪物沿用标题级别。其余文件按路径种类切。"""
    entries: list[CatalogEntry] = []
    unresolved: list[str] = []
    deferred: list[str] = []
    for path in sorted(HTML_ROOT.rglob("*.htm")):
        if is_skipped_html(path):
            continue
        location = _relative(path, HTML_ROOT)
        found, missing = _html_file(path, location)
        if not found and not missing and classify_html(location) != "skip":
            deferred.append(location)
        entries.extend(found)
        unresolved.extend(missing)
    kept, duplicates = _dedupe(entries)
    return kept, unresolved + duplicates, deferred


def _html_file(path: Path, location: str) -> tuple[list[CatalogEntry], list[str]]:
    parts = path.parts
    html = read_gbk_html(path)
    if SPELL_DIR_NAME in parts:
        return entries_from_spell_html(html, location)
    if MONSTER_BOOK_NAME in parts:
        return entries_from_monster_html(html, location)
    return entries_from_classified_html(html, location)


def collect_markdown(
    terms: dict[str, dict[str, str]],
) -> tuple[list[CatalogEntry], list[str], list[str]]:
    """一文件一条的用文件名。状态、专长、背景和职业按标题拆开。"""
    entries: list[CatalogEntry] = []
    unresolved: list[str] = []
    deferred: list[str] = []
    for path in sorted(MARKDOWN_ROOT.rglob("*.md")):
        location = _relative(path, MARKDOWN_ROOT.parent)
        found, missing, skipped = _markdown_file(path, location, terms)
        if skipped:
            deferred.append(location)
        entries.extend(found)
        unresolved.extend(missing)
    kept, duplicates = _dedupe(entries)
    return kept, unresolved + duplicates, deferred


def _markdown_file(
    path: Path,
    location: str,
    terms: dict[str, dict[str, str]],
) -> tuple[list[CatalogEntry], list[str], bool]:
    kind = section_kind(location)
    if kind:
        found, missing = section_entries(
            path.read_text(encoding="utf-8"),
            relative_path=location,
            kind=kind,
            terms=terms,
        )
        return found, missing, not found and not missing
    entry_type = folder_entry_type(location)
    if not entry_type:
        skipped = " (Alt)" not in location and not path.name.startswith("#")
        return [], [], skipped
    entry = entry_from_markdown_file(
        path.read_text(encoding="utf-8"),
        relative_path=location,
        entry_type=entry_type,
        english_name=path.stem,
    )
    if entry is None:
        return [], [], True
    return [entry], [], False


def _dedupe(entries: list[CatalogEntry]) -> tuple[list[CatalogEntry], list[str]]:
    seen: set[str] = set()
    kept: list[CatalogEntry] = []
    duplicates: list[str] = []
    for entry in entries:
        if entry.entry_id in seen:
            duplicates.append(f"{entry.location}#duplicate:{entry.entry_id}")
            continue
        seen.add(entry.entry_id)
        kept.append(entry)
    return kept, duplicates


def _write_jsonl(path: Path, rows: list[dict[str, str]]) -> None:
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _counts(entries: list[CatalogEntry]) -> dict[str, int]:
    return dict(sorted(Counter(item.entry_type for item in entries).items()))


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    html_entries, html_unresolved, html_deferred = collect_html()
    markdown_entries, markdown_unresolved, markdown_deferred = collect_markdown(
        term_index(html_entries),
    )
    _write_jsonl(OUTPUT_DIR / "src-srd52-zh.jsonl", [asdict(item) for item in html_entries])
    _write_jsonl(
        OUTPUT_DIR / "src-srd51-zh.jsonl",
        [asdict(item) for item in markdown_entries],
    )
    summary = {
        "src-srd52-zh": len(html_entries),
        "src-srd52-zh-by-type": _counts(html_entries),
        "src-srd52-zh-unresolved": html_unresolved,
        "src-srd52-zh-deferred": html_deferred,
        "src-srd51-zh": len(markdown_entries),
        "src-srd51-zh-by-type": _counts(markdown_entries),
        "src-srd51-zh-unresolved": markdown_unresolved,
        "src-srd51-zh-deferred": markdown_deferred,
    }
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"src-srd52-zh {len(html_entries)} {_counts(html_entries)}")
    print(f"src-srd52-zh unresolved {len(html_unresolved)}")
    print(f"src-srd52-zh deferred {len(html_deferred)}")
    print(f"src-srd51-zh {len(markdown_entries)} {_counts(markdown_entries)}")
    print(f"src-srd51-zh unresolved {len(markdown_unresolved)}")
    print(f"src-srd51-zh deferred {len(markdown_deferred)}")


if __name__ == "__main__":
    main()
