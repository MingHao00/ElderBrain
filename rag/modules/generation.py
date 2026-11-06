"""用外部对话接口根据父节原文作答。不在本机加载生成模型。"""

import secrets
from typing import Protocol

from generation_call import QWEN_MODEL, qwen_body, qwen_client
from generation_prompt import render_prompt
from modules.data_preparation import RagPiece
from scope import VERSION_SENTENCE

# 十六字节的随机边界，避免和规则原文里的普通句子撞上。
DELIMITER_NBYTES = 16
NO_EVIDENCE = VERSION_SENTENCE + "没有找到足够的原文。"


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
        extra_body: dict[str, object],
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
        self.model_name = QWEN_MODEL if model_name is None else model_name
        self.client = qwen_client() if client is None else client

    def answer(
        self,
        question: str,
        parents: list[RagPiece],
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """父节放进 system。历史排在当前问题前面。没有父节时不调用接口。"""
        if not parents:
            return NO_EVIDENCE
        delimiter = secrets.token_hex(DELIMITER_NBYTES)
        prior = history or []
        response = self.client.chat.completions.create(
            model=self.model_name,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            messages=_messages(question, parents, delimiter, prior),
            extra_body=qwen_body(),
        )
        return _message_text(response)


def _messages(
    question: str,
    parents: list[RagPiece],
    delimiter: str,
    history: list[dict[str, str]],
) -> list[dict[str, str]]:
    # 问题和原文分属两条消息，用户写进问题里的“原文”不会和证据接在一起。
    # 历史写进分隔符之间会被提示词当成规则原文，所以单独成消息。
    return [
        {"role": "system", "content": _system_prompt(parents, delimiter)},
        *_prior_messages(history),
        {"role": "user", "content": question},
    ]


def _prior_messages(history: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{"role": item["role"], "content": item["text"]} for item in history]


def _system_prompt(parents: list[RagPiece], delimiter: str) -> str:
    instructions = render_prompt(delimiter)
    return f"{instructions}\n\n{delimiter}\n{_context(parents)}\n{delimiter}"


def _context(parents: list[RagPiece]) -> str:
    # 提示词要求模型按这两行复制条目编号，不能再包在方括号里。
    blocks = [
        f"entry_id: {parent.metadata.get('entry_id', '')}\n"
        f"location: {parent.metadata.get('location', '')}\n"
        f"{parent.text}"
        for parent in parents
    ]
    return "\n\n".join(blocks)


def _message_text(response: _ChatResponse) -> str:
    if not response.choices:
        raise ValueError("生成接口没有返回正文")
    content = response.choices[0].message.content
    if not content or not content.strip():
        raise ValueError("生成接口没有返回正文")
    return content
