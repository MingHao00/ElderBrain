"""用已建好的索引跑中文 5.1 召回。不调用生成模型。"""

import json
import shutil
from dataclasses import asdict
from datetime import date
from pathlib import Path

from config import REPO_ROOT, RagConfig, require_env
from modules.api_embeddings import EMBEDDING_MODEL
from modules.data_preparation import RagPiece
from modules.index_construction import file_sha256
from modules.text_rerank import RERANK_MODEL
from recall_questions import EVAL_VERSION, SPLIT_DAILY, RecallQuestion, load_recall_questions
from recall_score import RECALL_AT_5, RECALL_AT_8, first_rank, is_hit, rate
from system import ElderBrainRag

ACCEPTANCE = REPO_ROOT / "design" / "05-acceptance.md"
CATALOG = REPO_ROOT / "parse" / "catalog" / "src-srd51-zh.jsonl"
CHUNK_FILE = REPO_ROOT / "parse" / "catalog" / "src-srd51-zh.chunks-1024.jsonl"
RUNS_DIR = REPO_ROOT / "runs"
RUN_ID = "2026-11-05-01"
TOP_RECORDED = RECALL_AT_8


def main() -> None:
    rag = ElderBrainRag(RagConfig(chunk_file=CHUNK_FILE))
    execute(RUN_ID, rag, require_env(EMBEDDING_MODEL))


def execute(run_id: str, rag: ElderBrainRag, embedding_model: str) -> None:
    """对已接好的索引跑同一套题。调用方决定索引目录和嵌入客户端。"""
    questions = load_recall_questions(ACCEPTANCE, CATALOG)
    rag.build_knowledge_base()
    rows = [_retrieve(rag, question) for question in questions]
    _write_run(RUNS_DIR / run_id, questions, rows, run_id, embedding_model, _index_dim(rag))
    _print_summary(rows)


def _index_dim(rag: ElderBrainRag) -> int:
    store = rag.index.vectorstore
    if store is None:
        raise ValueError("请先构建知识库")
    return int(store.index.d)


def _retrieve(rag: ElderBrainRag, question: RecallQuestion) -> dict[str, object]:
    if rag.retrieval is None:
        raise ValueError("请先构建知识库")
    candidates = rag.retrieval.hybrid_search(question.question)
    ranked_pieces = rag._rerank(question.question, candidates)[:TOP_RECORDED]
    ranked_ids = _entry_ids(ranked_pieces)
    return {
        "question_id": question.question_id,
        "split": question.split,
        "entry_ids": list(question.entry_ids),
        "ranked_entry_ids": ranked_ids,
        "hit_at_5": is_hit(question.entry_ids, ranked_ids, RECALL_AT_5),
        "hit_at_8": is_hit(question.entry_ids, ranked_ids, RECALL_AT_8),
        "rank": first_rank(question.entry_ids, ranked_ids),
    }


def _entry_ids(pieces: list[RagPiece]) -> list[str]:
    # 按重排后的块顺序记录。同一条目占多块时，前 5 条仍按块计。
    return [
        str(piece.metadata.get("entry_id", ""))
        for piece in pieces
        if piece.metadata.get("entry_id")
    ]


def _write_run(
    run_dir: Path,
    questions: list[RecallQuestion],
    rows: list[dict[str, object]],
    run_id: str,
    embedding_model: str,
    vector_dim: int,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "retrieval").mkdir(exist_ok=True)
    (run_dir / "scores").mkdir(exist_ok=True)
    shutil.copyfile(CHUNK_FILE, run_dir / "chunks.jsonl")
    _write_jsonl(run_dir / "queries.jsonl", [_query_row(item) for item in questions])
    _write_jsonl(run_dir / "retrieval" / "production.jsonl", rows)
    summary = _summary(rows)
    (run_dir / "scores" / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "manifest.md").write_text(
        _manifest(summary, run_id, embedding_model, vector_dim),
        encoding="utf-8",
    )


def _query_row(question: RecallQuestion) -> dict[str, object]:
    row = asdict(question)
    row["entry_ids"] = list(question.entry_ids)
    row["locations"] = list(question.locations)
    return row


def _summary(rows: list[dict[str, object]]) -> dict[str, object]:
    daily = [row for row in rows if row["split"] == SPLIT_DAILY]
    return {
        "eval_version": EVAL_VERSION,
        "question_count": len(rows),
        "daily_count": len(daily),
        "daily_recall_at_5": _group_rate(daily, "hit_at_5"),
        "daily_recall_at_8": _group_rate(daily, "hit_at_8"),
        "all_recall_at_5": _group_rate(rows, "hit_at_5"),
        "all_recall_at_8": _group_rate(rows, "hit_at_8"),
        "misses_at_5": [
            str(row["question_id"]) for row in rows if not row["hit_at_5"]
        ],
    }


def _group_rate(rows: list[dict[str, object]], field: str) -> float:
    if not rows:
        return 0.0
    return sum(1 for row in rows if row[field]) / len(rows)


def _manifest(
    summary: dict[str, object],
    run_id: str,
    embedding_model: str,
    vector_dim: int,
) -> str:
    digest = file_sha256(CHUNK_FILE)
    misses = "、".join(str(item) for item in summary["misses_at_5"]) or "无"
    return "\n".join([
        "# 召回运行",
        "",
        f"- 运行编号：{run_id}",
        f"- 日期：{date.today().isoformat()}",
        f"- 题目集：{EVAL_VERSION}",
        "- 路径：一条中文查询，向量加 BM25，再重排。不调用生成模型。",
        f"- 嵌入：{embedding_model}",
        f"- 维度：{vector_dim}",
        f"- 重排：{require_env(RERANK_MODEL)}",
        "- 分块：src-srd51-zh.chunks-1024.jsonl，分词器 cjk-word-v1",
        f"- 块文件 sha256：{digest}",
        f"- 日常题前 5 条召回：{summary['daily_recall_at_5']:.3f}",
        f"- 含放行题前 5 条召回：{summary['all_recall_at_5']:.3f}",
        f"- 前 5 条未命中：{misses}",
        "",
    ])


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _print_summary(rows: list[dict[str, object]]) -> None:
    summary = _summary(rows)
    print(
        "日常题 "
        f"{summary['daily_count']} 道，前 5 条召回 {summary['daily_recall_at_5']:.3f}"
    )
    print(f"含放行题前 5 条召回 {summary['all_recall_at_5']:.3f}")
    print(f"未命中：{'、'.join(str(item) for item in summary['misses_at_5']) or '无'}")


if __name__ == "__main__":
    main()
