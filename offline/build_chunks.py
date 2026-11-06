"""写出中文 5.1 的两份块清单。512 和 1024 都保留，不先选定。"""

import json
from dataclasses import asdict
from pathlib import Path

from chunks import (
    TOKENIZER_NAME,
    TOKEN_LIMITS,
    chunk_entries,
    chunk_list_checksum,
    count_cjk_words,
)
from html_entries import CatalogEntry

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = REPO_ROOT / "parse" / "catalog" / "src-srd51-zh.jsonl"
OUTPUT_DIR = REPO_ROOT / "parse" / "catalog"
SOURCE_ID = "src-srd51-zh"


def load_entries(path: Path) -> list[CatalogEntry]:
    """只读中文 5.1 目录。别的来源不进块清单。"""
    entries: list[CatalogEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("source_id") != SOURCE_ID:
            continue
        entries.append(CatalogEntry(**row))
    return entries


def write_limit(entries: list[CatalogEntry], limit: int) -> tuple[str, int, int]:
    chunks = chunk_entries(entries, token_limit=limit, count_tokens=count_cjk_words)
    path = OUTPUT_DIR / f"src-srd51-zh.chunks-{limit}.jsonl"
    lines = [json.dumps(asdict(item), ensure_ascii=False, sort_keys=True) for item in chunks]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    over_limit = sum(1 for item in chunks if item.token_count > limit)
    return chunk_list_checksum(chunks), len(chunks), over_limit


def write_manifest(stats: list[tuple[int, str, int, int]], source_pin: str) -> None:
    lines = [
        "# 中文 5.1 块清单",
        "",
        f"- 来源：`{SOURCE_ID}`，提交 `{source_pin}`",
        f"- 分词器：`{TOKENIZER_NAME}`。一个汉字计 1，一段连续英文或数字计 1。",
        "- 这不是嵌入模型的分词器。换计数方式就要重切。",
        "- 512 和 1024 两份都保留。实验选定之前，不把其中一档当成生产上限。",
        "",
        "| 上限 | 块数 | 仍超过上限 | sha256 |",
        "| --- | --- | --- | --- |",
    ]
    for limit, digest, count, over_limit in stats:
        lines.append(f"| {limit} | {count} | {over_limit} | `{digest}` |")
    path = OUTPUT_DIR / "src-srd51-zh.chunks.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    entries = load_entries(CATALOG_PATH)
    if not entries:
        raise SystemExit(f"没有读到 {SOURCE_ID} 的条目")
    stats: list[tuple[int, str, int, int]] = []
    for limit in TOKEN_LIMITS:
        digest, count, over_limit = write_limit(entries, limit)
        stats.append((limit, digest, count, over_limit))
        print(f"{limit} chunks={count} over={over_limit} {digest}")
    write_manifest(stats, entries[0].source_pin)


if __name__ == "__main__":
    main()
