"""测两家嵌入的单条查询耗时。预热后、批大小 1，不含重排。"""

import json
import os
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import faiss

from dotenv import load_dotenv

from config import DEFAULT_INDEX_DIR, REPO_ROOT
from embed_latency import P50, P95, percentile_linear, query_cost_yuan
from modules.api_embeddings import (
    COMPARE_EMBEDDING_API_KEY,
    EMBEDDING_API_KEY,
    ApiEmbeddings,
    load_api_embeddings,
    load_embedding3,
)
from modules.api_embeddings import _create_embeddings, _read_vectors
from recall_questions import SPLIT_DAILY, load_recall_questions
from run_embedding3 import INDEX_DIR as EMBEDDING3_INDEX_DIR
from run_recall import ACCEPTANCE, CATALOG, CHUNK_FILE

RUN_ID = "2026-11-05-03"
RUN_DIR = REPO_ROOT / "runs" / RUN_ID
WARMUP_TEXT = "预热"
MS_PER_SECOND = 1000
ERROR_TEXT_LIMIT = 500
# 2026-11-05 官网原价。华北2（北京）与智谱国内接口相同，都不含限时免费额度。
QWEN_YUAN_PER_MILLION = 0.5
EMBEDDING3_YUAN_PER_MILLION = 0.5
QWEN_MAX_INPUT_TOKENS = 131072
EMBEDDING3_STATED_INPUT_TOKENS = 3072
INDEX_FAISS_NAME = "index.faiss"


@dataclass(frozen=True)
class QueryTiming:
    """一次单条嵌入调用。"""

    question_id: str
    model_name: str
    elapsed_ms: float
    prompt_tokens: int
    yuan: float
    called_first: bool


def main() -> None:
    try:
        _run()
    except Exception as exc:
        print(_public_error(exc))
        raise SystemExit(1) from None


def _run() -> None:
    load_dotenv(REPO_ROOT / ".env")
    questions = [
        item for item in load_recall_questions(ACCEPTANCE, CATALOG)
        if item.split == SPLIT_DAILY
    ]
    qwen = load_api_embeddings()
    embedding3 = load_embedding3()
    _warmup(qwen)
    _warmup(embedding3)
    rows = _measure_all(questions, qwen, embedding3)
    _write(rows)
    _print(rows)


def _measure_all(
    questions: list,
    qwen: ApiEmbeddings,
    embedding3: ApiEmbeddings,
) -> list[QueryTiming]:
    rows: list[QueryTiming] = []
    for index, question in enumerate(questions):
        pair = (qwen, embedding3) if index % 2 == 0 else (embedding3, qwen)
        for called_first, embedder in zip((True, False), pair):
            rows.append(_time_question(
                embedder,
                question.question_id,
                question.question,
                called_first,
            ))
    return rows


def _time_question(
    embedder: ApiEmbeddings,
    question_id: str,
    text: str,
    called_first: bool,
) -> QueryTiming:
    elapsed_ms, prompt_tokens = _time_embed(embedder, text)
    price = _unit_price(embedder.model_name)
    return QueryTiming(
        question_id,
        embedder.model_name,
        elapsed_ms,
        prompt_tokens,
        query_cost_yuan(prompt_tokens, price),
        called_first,
    )


def _time_embed(embedder: ApiEmbeddings, text: str) -> tuple[float, int]:
    started = time.perf_counter()
    response = _create_embeddings(
        embedder.client,
        embedder.model_name,
        [text],
        embedder.dimensions,
    )
    elapsed_ms = (time.perf_counter() - started) * MS_PER_SECOND
    _read_vectors(response, 1)
    return elapsed_ms, _prompt_tokens(response)


def _warmup(embedder: ApiEmbeddings) -> None:
    _time_embed(embedder, WARMUP_TEXT)


def _prompt_tokens(response: object) -> int:
    usage = getattr(response, "usage", None)
    tokens = getattr(usage, "prompt_tokens", None)
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
        raise ValueError("嵌入接口没有返回输入 token 数")
    return tokens


def _unit_price(model_name: str) -> float:
    if model_name == "embedding-3":
        return EMBEDDING3_YUAN_PER_MILLION
    return QWEN_YUAN_PER_MILLION


def _write(rows: list[QueryTiming]) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    payload = [row.__dict__ for row in rows]
    lines = [json.dumps(item, ensure_ascii=False) for item in payload]
    (RUN_DIR / "latency.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (RUN_DIR / "manifest.md").write_text(_manifest(rows), encoding="utf-8")


def _manifest(rows: list[QueryTiming]) -> str:
    names = list(dict.fromkeys(row.model_name for row in rows))
    lines = [
        "# 嵌入单条查询耗时",
        "",
        f"- 运行编号：{RUN_ID}",
        f"- 日期：{date.today().isoformat()}",
        "- 样本：日常题 12 道，批大小 1。每题两家相邻调用，先后交替。预热各 1 次，不计入。",
        "- 计时：只含嵌入接口往返，不含重排。分位用线性插值，位置 (n-1)×p。",
        f"- 块文件：{CHUNK_FILE.name}，共 {_chunk_count()} 块",
        "",
    ]
    for name in names:
        lines.extend(_model_lines(name, rows))
    lines.extend(_decision(rows))
    lines.extend(_license_lines())
    return "\n".join(lines) + "\n"


def _model_lines(model_name: str, rows: list[QueryTiming]) -> list[str]:
    chosen = [row for row in rows if row.model_name == model_name]
    elapsed = [row.elapsed_ms for row in chosen]
    tokens = [row.prompt_tokens for row in chosen]
    mean_tokens = sum(tokens) / len(tokens)
    mean_yuan = sum(row.yuan for row in chosen) / len(chosen)
    return [
        f"## {model_name}",
        "",
        f"- P50：{percentile_linear(elapsed, P50):.1f} ms",
        f"- P95：{percentile_linear(elapsed, P95):.1f} ms",
        f"- 平均输入 token：{mean_tokens:.1f}",
        f"- 单次查询均价：{mean_yuan:.8f} 元",
        f"- 官网单价：{_unit_price(model_name)} 元/百万 tokens",
        f"- 截断块数：{_truncated(model_name)}",
        f"- 文档输入上限：{_input_limit(model_name)} tokens",
        "",
    ]


def _input_limit(model_name: str) -> int:
    if model_name == "embedding-3":
        return EMBEDDING3_STATED_INPUT_TOKENS
    return QWEN_MAX_INPUT_TOKENS


def _decision(rows: list[QueryTiming]) -> list[str]:
    names = list(dict.fromkeys(row.model_name for row in rows))
    if len(names) != 2:
        raise ValueError("耗时比较需要两家模型")
    left, right = names
    left_p95 = _p95(left, rows)
    right_p95 = _p95(right, rows)
    if left_p95 == right_p95:
        return ["## 决定", "", "- 两家 P95 相同，耗时没有分开。", ""]
    winner = left if left_p95 < right_p95 else right
    return [
        "## 决定",
        "",
        f"- 接口 P95 更低的是 {winner}。按第 6.5 节，这一步分出嵌入模型。",
        "",
    ]


def _p95(model_name: str, rows: list[QueryTiming]) -> float:
    elapsed = [row.elapsed_ms for row in rows if row.model_name == model_name]
    return percentile_linear(elapsed, P95)


def _license_lines() -> list[str]:
    return [
        "## 许可证",
        "",
        "- qwen3.7-text-embedding：阿里云百炼服务协议（2026-09-02）。付费推理可接入自有应用。",
        "- 不得转售该服务，也不得用输出训练与百炼模型竞争的产品。",
        "- embedding-3：智谱开放平台用户协议（2026-09-04）。付费功能付费后可按规则商用。",
        "- 许可是非排他、不可转让、不可转许可。可将接口集成进自有产品，不得转售或中转 API。",
        "- 两家都允许本项目这种调用。许可证这一步不分胜负。",
        "",
    ]


def _truncated(model_name: str) -> int:
    index_dir = EMBEDDING3_INDEX_DIR if model_name == "embedding-3" else DEFAULT_INDEX_DIR
    indexed = _indexed_count(index_dir / INDEX_FAISS_NAME)
    missing = _chunk_count() - indexed
    if missing < 0:
        raise ValueError("索引条数多于块清单")
    return missing


def _indexed_count(path: Path) -> int:
    if not path.is_file():
        raise ValueError(f"找不到索引: {path.name}")
    return int(faiss.read_index(str(path)).ntotal)


def _chunk_count() -> int:
    return sum(1 for line in CHUNK_FILE.read_text(encoding="utf-8").splitlines() if line.strip())


def _print(rows: list[QueryTiming]) -> None:
    names = list(dict.fromkeys(row.model_name for row in rows))
    for name in names:
        elapsed = [row.elapsed_ms for row in rows if row.model_name == name]
        print(
            f"{name} P50 {percentile_linear(elapsed, P50):.1f} ms，"
            f"P95 {percentile_linear(elapsed, P95):.1f} ms"
        )


def _public_error(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    for name in (EMBEDDING_API_KEY, COMPARE_EMBEDDING_API_KEY):
        secret = os.getenv(name, "").strip()
        if secret:
            text = text.replace(secret, "[密钥]")
    return text[:ERROR_TEXT_LIMIT]


if __name__ == "__main__":
    main()
