"""按题目重抽引用支持率之差。不调用模型。"""

import json
import random
from dataclasses import dataclass
from pathlib import Path

from embed_latency import percentile_linear

DRAWS = 10_000
SEED = 42
INTERVAL_LOW = 0.025
INTERVAL_HIGH = 0.975
LABEL_SUPPORTED = "支持"


@dataclass(frozen=True)
class QuestionSupport:
    """一道题上两家各自的支持句数和有引用句数。"""

    question_id: str
    left_supported: int
    left_cited: int
    right_supported: int
    right_cited: int


def support_diff(questions: list[QuestionSupport]) -> float | None:
    """左减右的引用支持率。某一家没有引用句时无法比较。"""
    left_hit, left_n, right_hit, right_n = _totals(questions)
    if left_n == 0 or right_n == 0:
        return None
    return left_hit / left_n - right_hit / right_n


def paired_diffs(
    questions: list[QuestionSupport],
    draws: int = DRAWS,
    seed: int = SEED,
) -> list[float]:
    """按题目有放回抽取。同一题的句子留在一起。"""
    if not questions:
        raise ValueError("没有可比较的题目")
    if draws < 1:
        raise ValueError("重抽次数至少为 1")
    picker = random.Random(seed)
    size = len(questions)
    diffs: list[float] = []
    for _ in range(draws):
        sample = [questions[picker.randrange(size)] for _ in range(size)]
        diff = support_diff(sample)
        if diff is not None:
            diffs.append(diff)
    return diffs


def interval(diffs: list[float]) -> tuple[float, float]:
    """10,000 个差值的 95% 区间。含 0 则视为打平。"""
    low = percentile_linear(diffs, INTERVAL_LOW)
    high = percentile_linear(diffs, INTERVAL_HIGH)
    return low, high


def questions_from_labels(
    rows: list[dict[str, object]],
    left_model: str,
    right_model: str,
) -> list[QuestionSupport]:
    """把逐句标签收成按题目编号排序的配对计数。"""
    grouped: dict[str, dict[str, list[int]]] = {}
    for row in rows:
        question_id = str(row["question_id"])
        model_name = str(row["model_name"])
        supported = 1 if row["label"] == LABEL_SUPPORTED else 0
        bucket = grouped.setdefault(question_id, {})
        counts = bucket.setdefault(model_name, [0, 0])
        counts[0] += supported
        counts[1] += 1
    pairs: list[QuestionSupport] = []
    for question_id in sorted(grouped):
        left = grouped[question_id].get(left_model, [0, 0])
        right = grouped[question_id].get(right_model, [0, 0])
        pairs.append(QuestionSupport(question_id, left[0], left[1], right[0], right[1]))
    return pairs


def _totals(questions: list[QuestionSupport]) -> tuple[int, int, int, int]:
    left_hit = left_n = right_hit = right_n = 0
    for item in questions:
        left_hit += item.left_supported
        left_n += item.left_cited
        right_hit += item.right_supported
        right_n += item.right_cited
    return left_hit, left_n, right_hit, right_n


def main() -> None:
    from config import REPO_ROOT
    from generation_call import DEEPSEEK_MODEL, QWEN_MODEL

    run_dir = REPO_ROOT / "runs" / "2026-11-06-02"
    rows = _load_jsonl(run_dir / "judge" / "subagent-labels.jsonl")
    questions = questions_from_labels(rows, DEEPSEEK_MODEL, QWEN_MODEL)
    diffs = paired_diffs(questions)
    low, high = interval(diffs)
    observed = support_diff(questions)
    payload = {
        "draws": DRAWS,
        "seed": SEED,
        "used_draws": len(diffs),
        "observed_diff": observed,
        "interval_low": low,
        "interval_high": high,
        "contains_zero": low <= 0 <= high,
        "questions": [
            {
                "question_id": item.question_id,
                "deepseek_supported": item.left_supported,
                "deepseek_cited": item.left_cited,
                "qwen_supported": item.right_supported,
                "qwen_cited": item.right_cited,
            }
            for item in questions
        ],
    }
    path = run_dir / "scores" / "bootstrap.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"差值 {observed:.4f}，区间 [{low:.4f}, {high:.4f}]，"
        f"{'含 0' if payload['contains_zero'] else '不含 0'}"
    )


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


if __name__ == "__main__":
    main()
