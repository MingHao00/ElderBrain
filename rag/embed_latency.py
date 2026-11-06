"""单条嵌入查询的分位和价格。只处理已经测到的数字。"""

import math

P50 = 0.50
P95 = 0.95
TOKENS_PER_MILLION = 1_000_000


def percentile_linear(samples: list[float], fraction: float) -> float:
    """线性插值分位。位置是 (n - 1) * fraction，n 为样本数。"""
    if not samples:
        raise ValueError("耗时样本不能为空")
    if fraction < 0 or fraction > 1:
        raise ValueError("分位必须在 0 和 1 之间")
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def query_cost_yuan(prompt_tokens: int, yuan_per_million: float) -> float:
    """按官网每百万 tokens 单价折成单次查询的人民币。"""
    if isinstance(prompt_tokens, bool) or not isinstance(prompt_tokens, int):
        raise ValueError("token 数必须是整数")
    if prompt_tokens < 0 or yuan_per_million < 0:
        raise ValueError("token 数和单价不能为负")
    return prompt_tokens * yuan_per_million / TOKENS_PER_MILLION
