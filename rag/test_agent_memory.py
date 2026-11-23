"""记忆、压缩和 memory_search。窗口数字只给测试用，不是生产输入窗口。"""

import pickle
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

_RAG_DIR = Path(__file__).resolve().parent
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

import agent_memory
from agent_memory import (
    COMPRESS_AT,
    KEEP_TAIL,
    MemoryArchive,
    checkpoint_serializer,
    compress_messages,
    memory_search,
    open_postgres_saver,
    thread_config,
)
WINDOW_TOKENS = 100
EARLY_TOKENS = 40
TAIL_TOKENS = 10
CONVERSATION = "conv-fireball"
OTHER = "conv-other"
REPLACED_A = "被换下的火球术规则甲"
REPLACED_B = "被换下的火球术规则乙"
TAIL_TEXT = "尾部原文保留"
OTHER_TEXT = "别人的火球术笔记"
SUMMARY = "这是摘要"
BLANK_QUERY = " \n\t"
PICKLE_TYPE = "pickle"
CONFIGURABLE_KEY = "configurable"
THREAD_ID_KEY = "thread_id"
FAKE_DATABASE_URL = "postgresql://db.example/elderbrain"
DATABASE_URL_ENV = "DATABASE_URL"
TOKEN_BY_TEXT = {
    REPLACED_A: EARLY_TOKENS,
    REPLACED_B: EARLY_TOKENS,
    TAIL_TEXT: TAIL_TOKENS,
}


def test_compress_ratios_match_the_plan() -> None:
    """压缩阈值和尾部比例使用计划里的常量。"""
    assert COMPRESS_AT == 0.85
    assert KEEP_TAIL == 0.10


def test_serializer_rejects_pickle_without_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    """反序列化走序列化器本身，pickle 载荷失败且不调用 pickle.loads。"""
    pickled = pickle.dumps({"secret": "no"})
    calls = {"count": 0}
    original = pickle.loads

    def counting(data: bytes) -> object:
        calls["count"] += 1
        return original(data)

    monkeypatch.setattr(pickle, "loads", counting)
    serializer = checkpoint_serializer()
    sample = {"正文": "还在"}
    restored = serializer.loads_typed(serializer.dumps_typed(sample))
    assert isinstance(serializer, JsonPlusSerializer)
    assert serializer.pickle_fallback is False
    assert restored == sample
    with pytest.raises(NotImplementedError):
        serializer.loads_typed((PICKLE_TYPE, pickled))
    assert calls["count"] == 0


def test_thread_id_equals_conversation_id() -> None:
    """线程配置里的 thread_id 就是对话号。"""
    assert thread_config(CONVERSATION) == {
        CONFIGURABLE_KEY: {THREAD_ID_KEY: CONVERSATION},
    }


def test_missing_database_url_does_not_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有 DATABASE_URL 时直接失败，不打开连接。"""
    monkeypatch.delenv(DATABASE_URL_ENV, raising=False)

    def fail(database_url: str) -> object:
        raise AssertionError("不应连接数据库")

    monkeypatch.setattr(agent_memory, "_connect", fail)
    with pytest.raises(ValueError):
        with open_postgres_saver():
            pass
    with pytest.raises(ValueError):
        with open_postgres_saver("  "):
            pass


def test_postgres_saver_receives_serializer(monkeypatch: pytest.MonkeyPatch) -> None:
    """构造 PostgresSaver 时传入 pickle_fallback=False 的序列化器。"""

    class FakeConnection:
        def close(self) -> None:
            return None

    seen: dict[str, object] = {}

    def fake_connect(database_url: str) -> FakeConnection:
        seen["url"] = database_url
        return FakeConnection()

    def spy_init(
        self: PostgresSaver,
        conn: object,
        pipe: object = None,
        serde: object = None,
    ) -> None:
        seen["serde"] = serde
        self.conn = conn
        self.pipe = pipe
        self.serde = serde

    monkeypatch.setattr(agent_memory, "_connect", fake_connect)
    monkeypatch.setattr(PostgresSaver, "__init__", spy_init)
    with open_postgres_saver(FAKE_DATABASE_URL) as saver:
        assert isinstance(saver.serde, JsonPlusSerializer)
        assert saver.serde.pickle_fallback is False
    assert seen["url"] == FAKE_DATABASE_URL
    passed = seen["serde"]
    assert isinstance(passed, JsonPlusSerializer)
    assert passed.pickle_fallback is False


def test_small_window_compresses_and_search_stays_in_conversation() -> None:
    """压过 85% 后上下文只留摘要和尾部，换下的原文只有本对话能搜到。"""
    archive = MemoryArchive()
    seen: list[list[str]] = []
    result = _compress(_long_messages(), archive, seen)
    texts = _texts(result)
    archive.add(OTHER, [OTHER_TEXT])
    assert seen == [[REPLACED_A, REPLACED_B]]
    assert SUMMARY in texts
    assert TAIL_TEXT in texts
    assert REPLACED_A not in texts
    assert REPLACED_B not in texts
    assert memory_search(archive, CONVERSATION, "火球术规则甲") == [REPLACED_A]
    assert memory_search(archive, OTHER, "火球术规则甲") == []
    assert memory_search(archive, CONVERSATION, "尾部原文") == []
    assert memory_search(archive, CONVERSATION, BLANK_QUERY) == []
    assert memory_search(archive, OTHER, BLANK_QUERY) == []


def test_archive_roundtrip_stays_searchable() -> None:
    """存档字节经同一个 JsonPlusSerializer 读回后，新对象仍能搜到。"""
    archive = MemoryArchive()
    _compress(_long_messages(), archive, [])
    serializer = checkpoint_serializer()
    encoded = serializer.dumps_typed(archive.payload())
    restored = MemoryArchive.from_payload(serializer.loads_typed(encoded))
    assert restored is not archive
    assert memory_search(restored, CONVERSATION, "火球术规则乙") == [REPLACED_B]
    assert memory_search(restored, OTHER, "火球术规则乙") == []



def test_non_positive_window_is_rejected() -> None:
    """窗口不是正数时拒绝压缩。"""
    with pytest.raises(ValueError):
        compress_messages(
            [],
            conversation_id=CONVERSATION,
            window_tokens=0,
            count_tokens=_count,
            summarize=lambda _messages: AIMessage(content=SUMMARY),
            archive=MemoryArchive(),
        )


def test_below_threshold_keeps_messages() -> None:
    """没到 85% 时不摘要，也不把原文放进存档。"""
    archive = MemoryArchive()
    seen: list[list[str]] = []
    messages = [HumanMessage(content=REPLACED_A), HumanMessage(content=TAIL_TEXT)]
    result = _compress(messages, archive, seen)
    assert _texts(result) == [REPLACED_A, TAIL_TEXT]
    assert seen == []
    assert memory_search(archive, CONVERSATION, "火球") == []


def _compress(
    messages: Sequence[BaseMessage],
    archive: MemoryArchive,
    seen: list[list[str]],
) -> list[BaseMessage]:
    def summarize(replaced: Sequence[BaseMessage]) -> AIMessage:
        seen.append(_texts(replaced))
        return AIMessage(content=SUMMARY)

    return compress_messages(
        messages,
        conversation_id=CONVERSATION,
        window_tokens=WINDOW_TOKENS,
        count_tokens=_count,
        summarize=summarize,
        archive=archive,
    )


def _count(messages: Sequence[BaseMessage]) -> int:
    total = 0
    for message in messages:
        content = message.content
        if not isinstance(content, str):
            raise AssertionError("测试消息必须是字符串")
        total += TOKEN_BY_TEXT[content]
    return total


def _long_messages() -> list[HumanMessage]:
    return [
        HumanMessage(content=REPLACED_A),
        HumanMessage(content=REPLACED_B),
        HumanMessage(content=TAIL_TEXT),
    ]


def _texts(messages: Sequence[BaseMessage]) -> list[str]:
    return [
        message.content
        for message in messages
        if isinstance(message.content, str)
    ]

