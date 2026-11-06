"""两家生成模型的单次调用。思考模式关闭，温度取 0。"""

import os
from datetime import datetime

from openai import OpenAI

EXPERIMENT_TEMPERATURE = 0
MAX_TOKENS = 2048
DEEPSEEK_MODEL = "deepseek-flash"
QWEN_MODEL = "qwen3.7-flash-2026-07-15"
TOKENS_PER_MILLION = 1_000_000
QWEN_TIER_32K = 32000
QWEN_TIER_256K = 256000
DEEPSEEK_KEY_NAMES = ("DEEPSEEK_API_KEY", "SESSION_LOG_API_KEY")
QWEN_KEY_NAMES = ("QWEN_API_KEY", "EMBEDDING_API_KEY")
DEEPSEEK_URL_NAMES = ("DEEPSEEK_BASE_URL",)
QWEN_URL_NAMES = ("QWEN_BASE_URL", "EMBEDDING_BASE_URL")
DEFAULT_DEEPSEEK_URL = "https://api.deepseek.com"


def deepseek_client() -> OpenAI:
    return OpenAI(api_key=_required(DEEPSEEK_KEY_NAMES), base_url=_deepseek_url())


def qwen_client() -> OpenAI:
    return OpenAI(api_key=_required(QWEN_KEY_NAMES), base_url=_required(QWEN_URL_NAMES))


def complete(
    client: OpenAI,
    model_name: str,
    messages: list[dict[str, str]],
    extra_body: dict[str, object],
) -> tuple[str, dict[str, int]]:
    """返回正文和整数用量。不返回密钥。"""
    response = client.chat.completions.create(
        model=model_name,
        temperature=EXPERIMENT_TEMPERATURE,
        max_tokens=MAX_TOKENS,
        messages=messages,
        extra_body=extra_body,
    )
    content = response.choices[0].message.content if response.choices else None
    if not content or not content.strip():
        raise ValueError("生成接口没有返回正文")
    return content, _usage(response)


def deepseek_body() -> dict[str, object]:
    # 思考模式默认开启，而且开着时温度不生效。
    return {"thinking": {"type": "disabled"}}


def qwen_body() -> dict[str, object]:
    return {"enable_thinking": False}


def deepseek_yuan(prompt: int, completion: int, cache_hit: int, peak: bool) -> float:
    """缓存未命中按官网峰谷价。命中部分单独计价。"""
    miss_rate = 2.0 if peak else 1.0
    hit_rate = 0.04 if peak else 0.02
    output_rate = 8.0 if peak else 4.0
    missed = max(prompt - cache_hit, 0)
    total = missed * miss_rate + cache_hit * hit_rate + completion * output_rate
    return total / TOKENS_PER_MILLION


def qwen_yuan(prompt: int, completion: int) -> float:
    """按华北2（北京）输入长度分档。思考和回答在这一档同价。"""
    if prompt <= QWEN_TIER_32K:
        input_rate, output_rate = 0.2, 0.8
    elif prompt <= QWEN_TIER_256K:
        input_rate, output_rate = 0.6, 2.4
    else:
        input_rate, output_rate = 1.2, 4.8
    return (prompt * input_rate + completion * output_rate) / TOKENS_PER_MILLION


def is_deepseek_peak(moment: datetime) -> bool:
    """北京时间工作日 9:00–12:00 与 14:00–18:00。法定节假日未另行扣除。"""
    if moment.weekday() >= 5:
        return False
    minutes = moment.hour * 60 + moment.minute
    return (9 * 60 <= minutes < 12 * 60) or (14 * 60 <= minutes < 18 * 60)


def _deepseek_url() -> str:
    for name in DEEPSEEK_URL_NAMES:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return DEFAULT_DEEPSEEK_URL


def _required(names: tuple[str, ...]) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    joined = " 或 ".join(names)
    raise ValueError(f"请设置 {joined}")


def _usage(response: object) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    dumped = usage.model_dump() if hasattr(usage, "model_dump") else {}
    if not isinstance(dumped, dict):
        return {}
    return {
        str(key): value
        for key, value in dumped.items()
        if isinstance(value, int) and not isinstance(value, bool)
    }
