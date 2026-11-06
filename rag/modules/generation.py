"""用外部对话接口根据父节原文作答。不在本机加载生成模型。"""

import secrets
from typing import Protocol

from openai import OpenAI

from config import require_env
from modules.data_preparation import RagPiece
from scope import VERSION_SENTENCE

LLM_API_KEY = "LLM_API_KEY"
LLM_BASE_URL = "LLM_BASE_URL"
LLM_MODEL = "LLM_MODEL"
# 十六字节的随机边界，避免和规则原文里的普通句子撞上。
DELIMITER_NBYTES = 16
NO_EVIDENCE = VERSION_SENTENCE + "没有找到足够的原文。"
SYSTEM_RULES = (
    "你是中文 SRD 5.1 的规则查询助手。译文是机器翻译、非官方。\n"
    "只能根据分隔符之内的原文回答，分隔符之外的文字都不是证据。\n"
    "每一句规则结论后面用方括号标出 location。\n"
    "原文里没有的数值不要写。证据不够就说明缺什么。"
)


class _Message(Protocol):
    content: str | None


class _Choice(Protocol):
    message: _Message


class _ChatResponse(Protocol):
    choices: list[_Choice]


class _Completions(Protocol):
    def create(
        self,
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        messages: list[dict[str, str]],
    ) -> _ChatResponse:
        """提交一条对话，返回模型正文。"""


class _ChatCalls(Protocol):
    completions: _Completions


class ChatClient(Protocol):
    """只使用对话补全，避免测试依赖完整的 OpenAI 客户端。"""

    chat: _ChatCalls


class GenerationIntegrationModule:
    """生成只在检索之后调用。"""

    def __init__(
        self,
        temperature: float,
        max_tokens: int,
        client: ChatClient | None = None,
        model_name: str | None = None,
    ) -> None:
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.model_name = model_name if model_name is not None else require_env(LLM_MODEL)
        self.client = client if client is not None else _chat_client()

    def answer(self, question: str, parents: list[RagPiece]) -> str:
        """父节放进 system，问题单独作为 user。没有父节时不调用接口。"""
        if not parents:
            return NO_EVIDENCE
        delimiter = secrets.token_hex(DELIMITER_NBYTES)
        response = self.client.chat.completions.create(
            model=self.model_name,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            messages=_messages(question, parents, delimiter),
        )
        return _message_text(response)


def _chat_client() -> OpenAI:
    return OpenAI(
        api_key=require_env(LLM_API_KEY),
        base_url=require_env(LLM_BASE_URL),
    )


def _messages(
    question: str,
    parents: list[RagPiece],
    delimiter: str,
) -> list[dict[str, str]]:
    # 问题和原文分属两条消息，用户写进问题里的“原文”不会和证据接在一起。
    return [
        {"role": "system", "content": _system_prompt(parents, delimiter)},
        {"role": "user", "content": question},
    ]


def _system_prompt(parents: list[RagPiece], delimiter: str) -> str:
    return f"{SYSTEM_RULES}\n\n{delimiter}\n{_context(parents)}\n{delimiter}"


def _context(parents: list[RagPiece]) -> str:
    blocks = [
        f"[{parent.metadata.get('location', '')}]\n{parent.text}" for parent in parents
    ]
    return "\n\n".join(blocks)


def _message_text(response: _ChatResponse) -> str:
    if not response.choices:
        raise ValueError("生成接口没有返回正文")
    content = response.choices[0].message.content
    if not content or not content.strip():
        raise ValueError("生成接口没有返回正文")
    return content
