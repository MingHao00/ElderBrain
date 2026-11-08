"""用外部对话接口根据父节原文作答。不在本机加载生成模型。"""

import secrets
from typing import Protocol

from generation_call import QWEN_MODEL, qwen_body, qwen_client
from generation_prompt import render_prompt
from modules.data_preparation import RagPiece
from scope import VERSION_SENTENCE
from session_card import SessionCard, generation_context

# 十六字节的随机边界，避免和规则原文里的普通句子撞上。
DELIMITER_NBYTES = 16
NO_EVIDENCE = VERSION_SENTENCE + "没有找到足够的原文。"
# 厂商没给用量时不估 token。记 0，回答仍然返回。
MISSING_TOKEN_COUNT = 0


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


class GeneratedText(str):
    """模型正文。用量缺字段时两个数都是 0，调用方不必再猜。"""

    prompt_tokens: int
    completion_tokens: int

    def __new__(
        cls,
        text: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> "GeneratedText":
        value = str.__new__(cls, text)
        value.prompt_tokens = prompt_tokens
        value.completion_tokens = completion_tokens
        return value


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
        card: SessionCard | None = None,
    ) -> str:
        """父节放进 system。卡片另起一条，不写进原文分隔区。没有父节时不调用接口。"""
        if not parents:
            return NO_EVIDENCE
        delimiter = secrets.token_hex(DELIMITER_NBYTES)
        response = self.client.chat.completions.create(
            model=self.model_name,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            messages=_messages(question, parents, delimiter, card),
            extra_body=qwen_body(),
        )
        text = _message_text(response)
        # 缺用量时两个数都是 0。不能编造，也不能把已经生成的正文变成失败。
        prompt, completion = _usage_tokens(response)
        return GeneratedText(text, prompt, completion)


def _messages(
    question: str,
    parents: list[RagPiece],
    delimiter: str,
    card: SessionCard | None,
) -> list[dict[str, str]]:
    # 问题和原文分属两条消息，用户写进问题里的“原文”不会和证据接在一起。
    # 卡片若写进分隔符之间，会被提示词当成规则原文。
    messages = [{"role": "system", "content": _system_prompt(parents, delimiter)}]
    context = generation_context(card)
    if context:
        messages.append({"role": "system", "content": context})
    messages.append({"role": "user", "content": question})
    return messages


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


def _usage_tokens(response: object) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    return (
        _one_token(usage, "prompt_tokens"),
        _one_token(usage, "completion_tokens"),
    )


def _one_token(usage: object, name: str) -> int:
    raw = _usage_field(usage, name)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return MISSING_TOKEN_COUNT
    return raw


def _usage_field(usage: object, name: str) -> object:
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage.get(name)
    value = getattr(usage, name, None)
    # 0 是合法用量。只有字段真的缺了，才去看 model_dump。
    if value is not None or not hasattr(usage, "model_dump"):
        return value
    dumped = usage.model_dump()
    if isinstance(dumped, dict):
        return dumped.get(name)
    return None


def _message_text(response: _ChatResponse) -> str:
    if not response.choices:
        raise ValueError("生成接口没有返回正文")
    content = response.choices[0].message.content
    if not content or not content.strip():
        raise ValueError("生成接口没有返回正文")
    return content
