"""临时：看中文 5.1 条目正文长度和二级标题。"""
import json
from collections import Counter
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "parse" / "catalog" / "src-srd51-zh.jsonl"
rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
print("entries", len(rows))
print(Counter(row["entry_type"] for row in rows))
longest = sorted(rows, key=lambda row: len(row["text"]), reverse=True)[:12]
for row in longest:
    h2 = row["text"].count("\n## ")
    print(len(row["text"]), h2, row["entry_type"], row["entry_id"])
