"""用已选定的嵌入和千问复核 20 道放行题。不改题目，也不改提示词。"""

import json

from config import REPO_ROOT, TOP_K
from generation_call import QWEN_MODEL, qwen_body, qwen_client
from generation_prompt import PROMPT
from generation_questions import load_holdout_questions
from recall_questions import SPLIT_HOLDOUT, RecallQuestion, load_recall_questions
from recall_score import first_rank, is_hit
from run_generation_experiment import (
    ACCEPTANCE,
    _call,
    _count,
    _freeze_from_rag,
    _latest,
    _model_summary,
    _needs_format_retry,
    _public_error,
    _rag,
    _write_jsonl,
)
from run_recall import CATALOG

RUN_ID = "2026-11-06-03"
RUN_DIR = REPO_ROOT / "runs" / RUN_ID
PHASE1_IDS = ("L09", "R10", "R16", "R20")


def main() -> None:
    try:
        _run()
    except Exception as exc:
        print(_public_error(exc))
        raise SystemExit(1) from None


def _run() -> None:
    if RUN_DIR.exists():
        raise FileExistsError(f"{RUN_ID} 已存在，不覆盖")
    questions = load_holdout_questions(ACCEPTANCE)
    rag = _rag()
    rag.build_knowledge_base()
    fixture, intercepts = _freeze_from_rag(questions, rag)
    print(f"夹具 {len(fixture)} 道，拦截 {len(intercepts)} 道")
    _write_jsonl(RUN_DIR / "fixture.jsonl", fixture)
    _write_jsonl(RUN_DIR / "intercept.jsonl", intercepts)
    rows = _ask_qwen(fixture)
    _write_generations(rows)
    (RUN_DIR / "prompt.md").write_text(PROMPT, encoding="utf-8")
    summary = _summary(rows, intercepts, _recall(fixture))
    scores_dir = RUN_DIR / "scores"
    scores_dir.mkdir(parents=True, exist_ok=True)
    (scores_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (RUN_DIR / "manifest.md").write_text(_manifest(summary), encoding="utf-8")
    _print_summary(summary)


def _ask_qwen(fixture: list[dict[str, object]]) -> list[dict[str, object]]:
    client = qwen_client()
    rows: list[dict[str, object]] = []
    for item in fixture:
        row = _call(client, QWEN_MODEL, qwen_body(), item, False)
        rows.append(row)
        if _needs_format_retry(row):
            rows.append(_call(client, QWEN_MODEL, qwen_body(), item, True))
        print(f"{item['question_id']} 已完成")
    return rows


def _write_generations(rows: list[dict[str, object]]) -> None:
    folder = RUN_DIR / "generations"
    folder.mkdir(parents=True, exist_ok=True)
    chosen = [row for row in rows if row["model_name"] == QWEN_MODEL]
    _write_jsonl(folder / f"{QWEN_MODEL}.jsonl", chosen)


def _recall(fixture: list[dict[str, object]]) -> dict[str, object]:
    questions = [
        item
        for item in load_recall_questions(ACCEPTANCE, CATALOG)
        if item.split == SPLIT_HOLDOUT
    ]
    by_id = {str(row["question_id"]): row for row in fixture}
    details = [_recall_row(question, by_id) for question in questions]
    hits = sum(1 for item in details if item["hit"])
    return {"count": len(details), "hits": hits, "questions": details}


def _recall_row(
    question: RecallQuestion,
    by_id: dict[str, dict[str, object]],
) -> dict[str, object]:
    item = by_id[question.question_id]
    parents = item["parents"] if isinstance(item["parents"], list) else []
    ranked = [
        str(parent.get("entry_id", ""))
        for parent in parents
        if isinstance(parent, dict)
    ]
    return {
        "question_id": question.question_id,
        "entry_ids": list(question.entry_ids),
        "hit": is_hit(question.entry_ids, ranked, TOP_K),
        "first_rank": first_rank(question.entry_ids, ranked),
    }


def _summary(
    rows: list[dict[str, object]],
    intercepts: list[dict[str, object]],
    recall: dict[str, object],
) -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "model_name": QWEN_MODEL,
        "intercept": intercepts,
        "sent": _model_summary(rows, QWEN_MODEL),
        "phase1": _phase1(rows),
        "recall": recall,
        "citation_support": "未裁判。裁判提示词与日常实验相同。",
    }


def _phase1(rows: list[dict[str, object]]) -> dict[str, object]:
    latest = [row for row in _latest(rows) if row["question_id"] in PHASE1_IDS]
    structured = [row for row in latest if row["structure_ok"]]
    clarify = [row for row in structured if row["require_clarify"]]
    return {
        "question_ids": list(PHASE1_IDS),
        "question_count": len(latest),
        "structure_ok": len(structured),
        "category_ok": _count(structured, "category_ok"),
        "version_failures": sum(1 for row in structured if not row["version_ok"]),
        "false_refusals": _count(structured, "false_refusal"),
        "dm_note_ok": _count(structured, "require_dm_ok"),
        "clarify_ok": _count(clarify, "clarify_ok"),
        # 这四道都期望回答，没有送到模型的拒答分母。
        "sent_refusals": 0,
    }


def _manifest(summary: dict[str, object]) -> str:
    sent = summary["sent"]
    phase1 = summary["phase1"]
    recall = summary["recall"]
    lines = [
        "# 放行复核",
        "",
        f"- 运行编号：{RUN_ID}",
        "- 嵌入：embedding-3。生成只调用 qwen3.7-flash-2026-07-15。",
        "- 提示词与 runs/2026-11-06-02 相同。温度 0，关闭思考模式。",
        "- 英文题、5.2 题、版本对比和版本意图仍跑过管道，不计入第一期门槛。",
        "- 第一期送到模型的题是 L09、R10、R16、R20。M02 的原文不入库，不进召回。",
        "- 引用是否被原文支持尚未裁判。",
        "",
        f"- 送到模型：结构 {sent['structure_ok']}/{sent['question_count']}，费用 {sent['yuan']} 元",
        (
            f"- 第一期：结构 {phase1['structure_ok']}/{phase1['question_count']}，"
            f"类别 {phase1['category_ok']}，版本混淆 {phase1['version_failures']}，"
            f"误拒 {phase1['false_refusals']}"
        ),
        f"- 召回：{recall['hits']}/{recall['count']}",
    ]
    return "\n".join(lines) + "\n"


def _print_summary(summary: dict[str, object]) -> None:
    sent = summary["sent"]
    phase1 = summary["phase1"]
    recall = summary["recall"]
    print(
        f"送到模型 {sent['structure_ok']}/{sent['question_count']}，费用 {sent['yuan']} 元"
    )
    print(
        f"第一期 {phase1['structure_ok']}/{phase1['question_count']}，"
        f"误拒 {phase1['false_refusals']}，召回 {recall['hits']}/{recall['count']}"
    )


if __name__ == "__main__":
    main()
