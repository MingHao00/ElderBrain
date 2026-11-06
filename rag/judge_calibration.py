"""从一次生成运行抽出有引用的句子，留给人工标注。"""

import json
from pathlib import Path

from generation_score import parse_generation
from judge_prompt import JUDGE_PROMPT, JUDGE_PROMPT_VERSION


def cited_items(raw: str, parents: list[object]) -> list[dict[str, object]]:
    """只保留带条目编号的句子。空拒答没有可裁判的引用。"""
    payload = parse_generation(raw)
    if payload is None:
        return []
    sources = _sources(parents)
    sentences = payload["sentences"]
    if not isinstance(sentences, list):
        return []
    items: list[dict[str, object]] = []
    for index, sentence in enumerate(sentences):
        item = _cited_item(index, sentence, sources)
        if item is not None:
            items.append(item)
    return items


def write_calibration(run_dir: Path) -> dict[str, object]:
    """写出待标句子和覆盖情况。人工标签留空。"""
    fixture = _load_jsonl(run_dir / "fixture.jsonl")
    by_question = {str(row["question_id"]): row for row in fixture}
    rows: list[dict[str, object]] = []
    coverage: list[dict[str, object]] = []
    folder = run_dir / "generations"
    for path in sorted(folder.glob("*.jsonl")):
        for generated in _load_jsonl(path):
            question_id = str(generated["question_id"])
            question = by_question[question_id]
            items = cited_items(str(generated["raw"]), _parents(question))
            coverage.append(_coverage_row(question_id, str(generated["model_name"]), items))
            rows.extend(_sheet_rows(question_id, str(generated["model_name"]), items))
    judge_dir = run_dir / "judge"
    judge_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(judge_dir / "calibration.jsonl", rows)
    (judge_dir / "prompt.md").write_text(JUDGE_PROMPT, encoding="utf-8")
    summary = {
        "judge_prompt_version": JUDGE_PROMPT_VERSION,
        "sentence_count": len(rows),
        "questions": coverage,
    }
    (judge_dir / "coverage.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_review(run_dir)
    return summary


def write_review(run_dir: Path) -> Path:
    """写成按题目阅读的稿子。评判只看句子是否被原文支持。"""
    questions = {
        str(row["question_id"]): str(row["question"])
        for row in _load_jsonl(run_dir / "fixture.jsonl")
    }
    rows = _load_jsonl(run_dir / "judge" / "calibration.jsonl")
    path = run_dir / "judge" / "calibration.md"
    path.write_text(review_markdown(rows, questions), encoding="utf-8")
    return path


def review_markdown(
    rows: list[dict[str, object]],
    questions: dict[str, str],
) -> str:
    """句子在前，原文在后。题目只供阅读，不作为评判对象。"""
    fence = _fence(rows)
    parts = [_review_intro(), ""]
    number = 1
    for question_id, group in _grouped(rows).items():
        question = questions.get(question_id, "")
        parts.append(f"## {question_id}")
        parts.append("")
        parts.append(f"题目：{question}")
        parts.append("")
        for row in group:
            parts.append(_review_item(number, row, fence))
            number += 1
    return "\n".join(parts)


def _review_intro() -> str:
    return "\n".join([
        "# 请评判：这句话是否被原文支持",
        "",
        "每条只判断加粗的句子能不能由下面的原文推出。",
        "换说法或调换语序，只要规则和数值都在原文里，写支持。",
        "句子多出原文没有的数值、条件、对象、例外或否定，写不支持。",
        "原文没谈到这句话，也写不支持。",
        "不要评文风、该不该拒答、答案完不完整，也不要比较两家模型。",
        "题目只帮助阅读，不参与评判。",
        "在「评判」后只写支持或不支持。请标出你认为不支持的句子。",
    ])


def _grouped(rows: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    groups: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        question_id = str(row["question_id"])
        groups.setdefault(question_id, []).append(row)
    return groups


def _review_item(number: int, row: dict[str, object], fence: str) -> str:
    source = str(row["source"]).strip()
    return "\n".join([
        f"### {number}. {row['model_name']}",
        "",
        "句子：",
        "",
        f"**{row['text']}**",
        "",
        f"条目：`{row['entry_id']}`",
        "",
        "原文：",
        "",
        f"{fence}text",
        source,
        fence,
        "",
        "评判：",
        "",
    ])


def _fence(rows: list[dict[str, object]]) -> str:
    longest = 2
    for row in rows:
        marks = 0
        for char in str(row["source"]):
            marks = marks + 1 if char == "`" else 0
            longest = max(longest, marks)
    return "`" * (longest + 1)


def _cited_item(
    index: int,
    sentence: object,
    sources: dict[str, str],
) -> dict[str, object] | None:
    if not isinstance(sentence, dict):
        return None
    entry_id = sentence.get("entry_id")
    if not isinstance(entry_id, str) or entry_id not in sources:
        return None
    return {
        "sentence_index": index,
        "entry_id": entry_id,
        "text": str(sentence.get("text", "")),
        "source": sources[entry_id],
    }


def _sources(parents: list[object]) -> dict[str, str]:
    sources: dict[str, str] = {}
    for parent in parents:
        if not isinstance(parent, dict):
            continue
        entry_id = str(parent.get("entry_id", ""))
        if not entry_id:
            continue
        sources[entry_id] = sources.get(entry_id, "") + "\n" + str(parent.get("text", ""))
    return sources


def _sheet_rows(
    question_id: str,
    model_name: str,
    items: list[dict[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in items:
        rows.append({
            "question_id": question_id,
            "model_name": model_name,
            "sentence_index": item["sentence_index"],
            "entry_id": item["entry_id"],
            "text": item["text"],
            "source": item["source"],
            "human_label": None,
        })
    return rows


def _coverage_row(
    question_id: str,
    model_name: str,
    items: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "question_id": question_id,
        "model_name": model_name,
        "cited_sentences": len(items),
    }


def _parents(question: dict[str, object]) -> list[object]:
    parents = question.get("parents")
    if isinstance(parents, list):
        return parents
    return []


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
    return rows


def main() -> None:
    from config import REPO_ROOT

    summary = write_calibration(REPO_ROOT / "runs" / "2026-11-06-02")
    print(f"待标句子 {summary['sentence_count']}")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


if __name__ == "__main__":
    main()
