"""用同一份 embedding-3 夹具比较两家生成模型。不使用未校准的裁判。"""

import json
import os
import secrets
from datetime import datetime
from pathlib import Path

from config import REPO_ROOT, TOP_K, RagConfig
from generation_call import (
    DEEPSEEK_MODEL,
    QWEN_MODEL,
    complete,
    deepseek_body,
    deepseek_client,
    deepseek_yuan,
    is_deepseek_peak,
    qwen_body,
    qwen_client,
    qwen_yuan,
)
from generation_prompt import FORMAT_HINT, PROMPT, render_prompt
from generation_questions import ROLE_INTERCEPT, GenerationQuestion, load_generation_questions
from generation_score import parse_generation, score_generation
from modules.data_preparation import RagPiece
from run_embedding3 import INDEX_DIR
from run_recall import CHUNK_FILE
from scope import refusal_for
from system import ElderBrainRag

RUN_ID = "2026-11-06-02"
PRIOR_RUN_ID = "2026-11-06-01"
RUN_DIR = REPO_ROOT / "runs" / RUN_ID
ACCEPTANCE = REPO_ROOT / "design" / "05-acceptance.md"
DELIMITER_NBYTES = 16
ERROR_TEXT_LIMIT = 500


def main() -> None:
    try:
        _run()
    except Exception as exc:
        print(_public_error(exc))
        raise SystemExit(1) from None


def _run() -> None:
    questions = load_generation_questions(ACCEPTANCE)
    fixture, intercepts = _freeze(questions)
    print(f"夹具 {len(fixture)} 道，拦截 {len(intercepts)} 道")
    _write_jsonl(RUN_DIR / "fixture.jsonl", fixture)
    _write_jsonl(RUN_DIR / "intercept.jsonl", intercepts)
    rows = _ask_all(fixture)
    _write_generations(rows)
    (RUN_DIR / "prompt.md").write_text(PROMPT, encoding="utf-8")
    summary = _summary(rows, intercepts)
    scores_dir = RUN_DIR / "scores"
    scores_dir.mkdir(parents=True, exist_ok=True)
    (scores_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (RUN_DIR / "manifest.md").write_text(_manifest(summary), encoding="utf-8")
    _print_summary(summary)


def _freeze(
    questions: list[GenerationQuestion],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    prior = _prior_parents()
    if prior is not None:
        print(f"沿用 {PRIOR_RUN_ID} 的检索夹具")
        return _freeze_from_prior(questions, prior)
    rag = _rag()
    rag.build_knowledge_base()
    return _freeze_from_rag(questions, rag)


def _prior_parents() -> dict[str, list[object]] | None:
    path = REPO_ROOT / "runs" / PRIOR_RUN_ID / "fixture.jsonl"
    if not path.is_file():
        return None
    parents: dict[str, list[object]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        item_parents = row.get("parents")
        if isinstance(item_parents, list):
            parents[str(row["question_id"])] = item_parents
    return parents


def _freeze_from_prior(
    questions: list[GenerationQuestion],
    prior: dict[str, list[object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    fixture: list[dict[str, object]] = []
    intercepts: list[dict[str, object]] = []
    for question in questions:
        if _is_intercept(question):
            intercepts.append(_intercept_row(question))
            continue
        parents = prior.get(question.question_id)
        if not isinstance(parents, list):
            raise ValueError(f"上一轮夹具缺少 {question.question_id}")
        fixture.append(_expectation_row(question, parents))
    return fixture, intercepts


def _freeze_from_rag(
    questions: list[GenerationQuestion],
    rag: ElderBrainRag,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    fixture: list[dict[str, object]] = []
    intercepts: list[dict[str, object]] = []
    for question in questions:
        if _is_intercept(question):
            intercepts.append(_intercept_row(question))
            continue
        fixture.append(_fixture_row(rag, question))
    return fixture, intercepts


def _is_intercept(question: GenerationQuestion) -> bool:
    return question.role == ROLE_INTERCEPT or refusal_for(question.question) is not None


def _rag() -> ElderBrainRag:
    from modules.api_embeddings import load_embedding3

    embedder = load_embedding3()
    rag = ElderBrainRag(RagConfig(chunk_file=CHUNK_FILE, index_dir=INDEX_DIR))
    rag.index.embeddings = embedder
    return rag


def _fixture_row(rag: ElderBrainRag, question: GenerationQuestion) -> dict[str, object]:
    parents = [_parent_row(parent) for parent in _parents(rag, question.question)]
    return _expectation_row(question, parents)


def _expectation_row(
    question: GenerationQuestion,
    parents: list[object],
) -> dict[str, object]:
    return {
        "question_id": question.question_id,
        "question": question.question,
        "expected_type": question.expected_type,
        "expected_category": question.expected_category,
        "require_dm": question.require_dm,
        "require_clarify": question.require_clarify,
        "parents": parents,
    }


def _parents(rag: ElderBrainRag, question: str) -> list[RagPiece]:
    if rag.retrieval is None:
        raise ValueError("请先构建知识库")
    ranked = rag._rerank(question, rag.retrieval.hybrid_search(question))[:TOP_K]
    return rag.data.parent_sections(ranked)


def _parent_row(parent: RagPiece) -> dict[str, str]:
    return {
        "entry_id": str(parent.metadata.get("entry_id", "")),
        "location": str(parent.metadata.get("location", "")),
        "section_id": str(parent.metadata.get("section_id", "")),
        "text": parent.text,
    }


def _intercept_row(question: GenerationQuestion) -> dict[str, object]:
    return {
        "question_id": question.question_id,
        "question": question.question,
        "blocked": refusal_for(question.question) is not None,
    }


def _ask_all(fixture: list[dict[str, object]]) -> list[dict[str, object]]:
    deepseek = deepseek_client()
    qwen = qwen_client()
    rows: list[dict[str, object]] = []
    for item in fixture:
        pair = [
            _call(deepseek, DEEPSEEK_MODEL, deepseek_body(), item, False),
            _call(qwen, QWEN_MODEL, qwen_body(), item, False),
        ]
        rows.extend(pair)
        if any(_needs_format_retry(row) for row in pair):
            rows.extend([
                _call(deepseek, DEEPSEEK_MODEL, deepseek_body(), item, True),
                _call(qwen, QWEN_MODEL, qwen_body(), item, True),
            ])
        print(f"{item['question_id']} 已完成")
    return rows


def _call(
    client: object,
    model_name: str,
    extra_body: dict[str, object],
    item: dict[str, object],
    retry: bool,
) -> dict[str, object]:
    moment = datetime.now()
    try:
        text, usage = complete(
            client,
            model_name,
            _messages(item, retry),
            extra_body,
        )
    except Exception as exc:
        text, usage = _public_error(exc), {}
        row = _scored(item, model_name, text, usage, moment, retry)
        row["api_error"] = True
        return row
    row = _scored(item, model_name, text, usage, moment, retry)
    row["api_error"] = False
    return row


def _needs_format_retry(row: dict[str, object]) -> bool:
    return not row["structure_ok"] and not row["api_error"]


def _messages(item: dict[str, object], retry: bool) -> list[dict[str, str]]:
    delimiter = secrets.token_hex(DELIMITER_NBYTES)
    system = render_prompt(delimiter)
    if retry:
        system = f"{system}\n{FORMAT_HINT}"
    parents = item["parents"]
    if not isinstance(parents, list):
        raise ValueError("夹具缺少父节")
    body = _context(parents)
    return [
        {"role": "system", "content": f"{system}\n\n{delimiter}\n{body}\n{delimiter}"},
        {"role": "user", "content": str(item["question"])},
    ]


def _context(parents: list[object]) -> str:
    blocks: list[str] = []
    for parent in parents:
        if not isinstance(parent, dict):
            continue
        blocks.append(
            f"entry_id: {parent.get('entry_id', '')}\n"
            f"location: {parent.get('location', '')}\n"
            f"{parent.get('text', '')}"
        )
    return "\n\n".join(blocks)


def _scored(
    item: dict[str, object],
    model_name: str,
    text: str,
    usage: dict[str, int],
    moment: datetime,
    retry: bool,
) -> dict[str, object]:
    payload = parse_generation(text)
    parents = item["parents"] if isinstance(item["parents"], list) else []
    allowed, sources = _parent_index(parents)
    score = score_generation(
        payload,
        str(item["expected_type"]),
        _optional_str(item.get("expected_category")),
        bool(item["require_dm"]),
        bool(item.get("require_clarify")),
        allowed,
        sources,
    )
    peak = is_deepseek_peak(moment)
    return {
        "question_id": item["question_id"],
        "model_name": model_name,
        "retry": retry,
        "raw": text,
        "usage": usage,
        "yuan": _yuan(model_name, usage, peak),
        "peak": peak,
        "structure_ok": score.structure_ok,
        "citation_scope_ok": score.citation_scope_ok,
        "numbers_ok": score.numbers_ok,
        "version_ok": score.version_ok,
        "category_ok": score.category_ok,
        "false_refusal": score.false_refusal,
        "require_dm_ok": score.require_dm_ok,
        "require_clarify": bool(item.get("require_clarify")),
        "clarify_ok": score.require_clarify_ok,
    }


def _parent_index(parents: list[object]) -> tuple[set[str], dict[str, str]]:
    allowed: set[str] = set()
    sources: dict[str, str] = {}
    for parent in parents:
        if not isinstance(parent, dict):
            continue
        entry_id = str(parent.get("entry_id", ""))
        if not entry_id:
            continue
        allowed.add(entry_id)
        sources[entry_id] = sources.get(entry_id, "") + "\n" + str(parent.get("text", ""))
    return allowed, sources


def _yuan(model_name: str, usage: dict[str, int], peak: bool) -> float:
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    if model_name == DEEPSEEK_MODEL:
        cache_hit = usage.get("prompt_cache_hit_tokens", 0)
        return deepseek_yuan(prompt, completion, cache_hit, peak)
    return qwen_yuan(prompt, completion)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _write_generations(rows: list[dict[str, object]]) -> None:
    folder = RUN_DIR / "generations"
    folder.mkdir(parents=True, exist_ok=True)
    for model_name in (DEEPSEEK_MODEL, QWEN_MODEL):
        chosen = [row for row in rows if row["model_name"] == model_name]
        _write_jsonl(folder / f"{model_name}.jsonl", chosen)


def _summary(
    rows: list[dict[str, object]],
    intercepts: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "intercept": intercepts,
        "models": [_model_summary(rows, name) for name in (DEEPSEEK_MODEL, QWEN_MODEL)],
        "citation_support": "未裁判。人工校准之前不用自动分做决定。",
    }


def _model_summary(rows: list[dict[str, object]], model_name: str) -> dict[str, object]:
    chosen = [row for row in rows if row["model_name"] == model_name]
    latest = _latest(chosen)
    structured = [row for row in latest if row["structure_ok"]]
    return {
        "model_name": model_name,
        "calls": len(chosen),
        "yuan": round(sum(float(row["yuan"]) for row in chosen), 6),
        "question_count": len(latest),
        "structure_ok": len(structured),
        "citation_scope_ok": _count(structured, "citation_scope_ok"),
        "numbers_ok": _count(structured, "numbers_ok"),
        "version_failures": sum(1 for row in structured if not row["version_ok"]),
        "category_ok": _count(structured, "category_ok"),
        "false_refusals": _count(structured, "false_refusal"),
        "dm_note_ok": _count(structured, "require_dm_ok"),
        "clarify_ok": _count(
            [row for row in structured if row["require_clarify"]],
            "clarify_ok",
        ),
    }


def _latest(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    by_question: dict[str, dict[str, object]] = {}
    for row in rows:
        by_question[str(row["question_id"])] = row
    return list(by_question.values())


def _count(rows: list[dict[str, object]], field: str) -> int:
    return sum(1 for row in rows if row[field])


def _manifest(summary: dict[str, object]) -> str:
    lines = [
        "# 生成模型比较",
        "",
        f"- 运行编号：{RUN_ID}",
        f"- 嵌入：embedding-3。检索夹具沿用 {PRIOR_RUN_ID}，温度 0，两家都关闭思考模式。",
        "- 引用是否被原文支持尚未裁判，不据此决定胜负。",
        "- 数值检查不把条目编号里的 51 当成规则数字。",
        "",
    ]
    models = summary["models"]
    if isinstance(models, list):
        for item in models:
            if isinstance(item, dict):
                lines.append(
                    f"- {item['model_name']}：结构 {item['structure_ok']}/{item['question_count']}，"
                    f"费用 {item['yuan']} 元"
                )
    return "\n".join(lines) + "\n"


def _print_summary(summary: dict[str, object]) -> None:
    models = summary["models"]
    if not isinstance(models, list):
        return
    for item in models:
        if isinstance(item, dict):
            print(
                f"{item['model_name']} 结构 {item['structure_ok']}/{item['question_count']}，"
                f"费用 {item['yuan']} 元"
            )


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _public_error(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    for name in ("SESSION_LOG_API_KEY", "EMBEDDING_API_KEY", "DEEPSEEK_API_KEY", "QWEN_API_KEY"):
        secret = os.getenv(name, "").strip()
        if secret:
            text = text.replace(secret, "[密钥]")
    return text[:ERROR_TEXT_LIMIT]


if __name__ == "__main__":
    main()
