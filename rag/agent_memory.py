"""对话记忆：检查点序列化、压缩，以及被换下原文的搜索。

输入窗口由调用方传入。本模块不保存生产窗口。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager

from langchain_core.messages import BaseMessage
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import Connection
from psycopg.rows import dict_row

COMPRESS_AT = 0.85
KEEP_TAIL = 0.10
DATABASE_URL_ENV = "DATABASE_URL"
CONFIGURABLE_KEY = "configurable"
THREAD_ID_KEY = "thread_id"
CONVERSATION_ID_FIELD = "conversation_id"
TEXT_FIELD = "text"
# 与 PostgresSaver.from_conn_string 一致，避免连接上留下预编译语句。
PREPARE_THRESHOLD = 0

CountTokens = Callable[[Sequence[BaseMessage]], int]
Summarize = Callable[[Sequence[BaseMessage]], BaseMessage]


class MemoryArchive:
    """本进程里可搜索的换下原文。字节读回后用新对象继续搜。"""

    def __init__(self, entries: Sequence[tuple[str, str]] | None = None) -> None:
        self._entries: list[tuple[str, str]] = list(entries or ())

    def add(self, conversation_id: str, texts: Sequence[str]) -> None:
        """把原文记在这个对话下。就地修改，供同一份存档上的工具立刻搜到。"""
        for text in texts:
            self._entries.append((conversation_id, text))

    def payload(self) -> list[dict[str, str]]:
        """交给 JsonPlusSerializer 的纯数据。"""
        return [
            {CONVERSATION_ID_FIELD: conversation_id, TEXT_FIELD: text}
            for conversation_id, text in self._entries
        ]

    @classmethod
    def from_payload(cls, payload: object) -> MemoryArchive:
        """从往返后的纯数据重建。字段类型不对时抛出 TypeError。"""
        return cls(_payload_rows(payload))

    @property
    def entries(self) -> tuple[tuple[str, str], ...]:
        """已记入的对话号和原文，按写入顺序。"""
        return tuple(self._entries)


def checkpoint_serializer() -> JsonPlusSerializer:
    """检查点序列化器。pickle 回退明确关闭，不使用构造默认值。

    Returns:
        pickle_fallback 为 False 的 JsonPlusSerializer。
    """
    # 默认已经是 False。写明是为了默认值以后改掉时，这里仍然拒绝 pickle。
    return JsonPlusSerializer(pickle_fallback=False)


def thread_config(conversation_id: str) -> dict[str, dict[str, str]]:
    """线程号等于对话号。

    Args:
        conversation_id: 对话号。

    Returns:
        只含 thread_id 的 LangGraph 配置。
    """
    return {CONFIGURABLE_KEY: {THREAD_ID_KEY: conversation_id}}


def _database_url(explicit: str | None) -> str:
    if explicit is not None:
        return explicit.strip()
    return os.environ.get(DATABASE_URL_ENV, "").strip()


def _connect(database_url: str) -> Connection:
    return Connection.connect(
        database_url,
        autocommit=True,
        prepare_threshold=PREPARE_THRESHOLD,
        row_factory=dict_row,
    )


@contextmanager
def open_postgres_saver(
    database_url: str | None = None,
) -> Iterator[PostgresSaver]:
    """有数据库地址才连接，并把关闭 pickle 的序列化器传进构造。

    from_conn_string 不会接收序列化器，所以这里直接构造。

    Args:
        database_url: 连接串。省略时读取 DATABASE_URL。

    Yields:
        已绑定序列化器的 PostgresSaver。

    Raises:
        ValueError: 地址为空。此时不会连接数据库。
    """
    url = _database_url(database_url)
    if not url:
        raise ValueError(f"请设置 {DATABASE_URL_ENV}")
    connection = _connect(url)
    try:
        yield PostgresSaver(connection, serde=checkpoint_serializer())
    finally:
        connection.close()


def memory_search(
    archive: MemoryArchive,
    conversation_id: str,
    query: str,
) -> list[str]:
    """只搜索这一对话被换下的原文。空白查询返回空列表。

    Args:
        archive: 存档。
        conversation_id: 当前对话号。别的对话的原文不会返回。
        query: 查询词。去掉空白后为空则不查。

    Returns:
        含查询词的原文，按写入顺序。
    """
    needle = query.strip()
    if not needle:
        return []
    return [
        text
        for owner, text in archive.entries
        if owner == conversation_id and needle in text
    ]


def compress_messages(
    messages: Sequence[BaseMessage],
    *,
    conversation_id: str,
    window_tokens: int,
    count_tokens: CountTokens,
    summarize: Summarize,
    archive: MemoryArchive,
) -> list[BaseMessage]:
    """达到窗口的 COMPRESS_AT 时，把较早消息收成一条摘要。

    尾部按 KEEP_TAIL 的 token 预算留原文。被换下的原文写入 archive。
    未达阈值时原样返回，不调用 summarize。

    Args:
        messages: 当前上下文。
        conversation_id: 这些原文所属的对话。
        window_tokens: 调用方给出的输入窗口。测试使用很小的正数。
        count_tokens: 对一段消息计数。
        summarize: 只接收被换下的消息，返回一条摘要。
        archive: 接收被换下原文的存档。

    Returns:
        摘要加尾部原文；未压缩时是原消息的副本。

    Raises:
        ValueError: 窗口不是正数。
    """
    _require_window(window_tokens)
    if not _needs_compress(messages, window_tokens, count_tokens):
        return list(messages)
    tail_start = _tail_start(messages, count_tokens, window_tokens * KEEP_TAIL)
    replaced = list(messages[:tail_start])
    if not replaced:
        return list(messages)
    archive.add(conversation_id, [_message_text(message) for message in replaced])
    return [summarize(replaced), *messages[tail_start:]]


def _require_window(window_tokens: int) -> None:
    if window_tokens <= 0:
        raise ValueError("输入窗口必须是正数")


def _needs_compress(
    messages: Sequence[BaseMessage],
    window_tokens: int,
    count_tokens: CountTokens,
) -> bool:
    return count_tokens(messages) >= window_tokens * COMPRESS_AT


def _tail_start(
    messages: Sequence[BaseMessage],
    count_tokens: CountTokens,
    tail_budget: float,
) -> int:
    """返回尾部起点。起点之前的消息会被换下。"""
    start = len(messages)
    for index in range(len(messages) - 1, -1, -1):
        if count_tokens(messages[index:]) > tail_budget:
            break
        start = index
    return start


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return ""


def _payload_rows(payload: object) -> list[tuple[str, str]]:
    if not isinstance(payload, list):
        raise TypeError("存档载荷必须是列表")
    return [_payload_row(item) for item in payload]


def _payload_row(item: object) -> tuple[str, str]:
    if not isinstance(item, dict):
        raise TypeError("存档条目必须是字典")
    conversation_id = item.get(CONVERSATION_ID_FIELD)
    text = item.get(TEXT_FIELD)
    if isinstance(conversation_id, str) and isinstance(text, str):
        return conversation_id, text
    raise TypeError("存档条目字段类型不对")
