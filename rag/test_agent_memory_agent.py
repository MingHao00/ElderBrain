"""memory_search 计入 5 次上限，以及有数据库时的检查点往返。"""

import os
import sys
import uuid
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

_RAG_DIR = Path(__file__).resolve().parent
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

import agent_memory
import web_agent
from agent_memory import MemoryArchive, open_postgres_saver, thread_config
from rag_search import PinnedIndex
from test_web_agent import RecordingParents, RecordingWeb, ScriptedChatModel
from web_agent import TOOL_RUN_LIMIT, build_web_agent

CONVERSATION = "conv-fireball"
QUESTION = "火球术怎么施放？"
LIMIT_ANSWER = "次数用完后的正文。"
MEMORY_TOOL = "memory_search"
PINNED_VERSION = "pinned-memory"
RECURSION_LIMIT = 40
CONFIGURABLE_KEY = "configurable"
THREAD_ID_KEY = "thread_id"
CHECKPOINT_NS_KEY = "checkpoint_ns"
EMPTY_NS = ""
NOTE_KEY = "note"
NOTE_TEXT = "已提交的正文"
METADATA_SOURCE = "input"
METADATA_STEP = 1
DATABASE_URL_ENV = "DATABASE_URL"
CALL_PREFIX = "call"
QUERY_PREFIX = "q"
BLOCKED_CALL_COUNT = TOOL_RUN_LIMIT + 1


def test_memory_search_is_on_the_agent_and_shares_the_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """memory_search 在工具名单里，第 6 次和其他工具共用 5 次上限。"""
    archive = MemoryArchive()
    queries = [f"{QUERY_PREFIX}{index}" for index in range(1, BLOCKED_CALL_COUNT + 1)]
    script = [_memory_call(query, index) for index, query in enumerate(queries, start=1)]
    script.append(AIMessage(content=LIMIT_ANSWER))
    model = ScriptedChatModel(script=script)
    calls: list[tuple[str, str]] = []
    real = agent_memory.memory_search

    def spy(store: MemoryArchive, conversation_id: str, query: str) -> list[str]:
        calls.append((conversation_id, query))
        return real(store, conversation_id, query)

    monkeypatch.setattr(web_agent.agent_memory, "memory_search", spy)
    _invoke(model, archive, CONVERSATION)
    assert model.bound_names == ["rag_search", "web_search", MEMORY_TOOL]
    expected = [(CONVERSATION, query) for query in queries[:TOOL_RUN_LIMIT]]
    assert calls == expected


@pytest.mark.skipif(
    not os.environ.get(DATABASE_URL_ENV, "").strip(),
    reason="没有 DATABASE_URL，只跳过实库写入",
)
def test_postgres_checkpoint_roundtrip() -> None:
    """有 DATABASE_URL 时，用测试库写入再读回已提交正文。"""
    conversation_id = f"agent-memory-{uuid.uuid4()}"
    write_config = thread_config(conversation_id)
    write_config[CONFIGURABLE_KEY][CHECKPOINT_NS_KEY] = EMPTY_NS
    checkpoint = {
        "ts": "2026-11-22T10:00:00+00:00",
        "id": str(uuid.uuid4()),
        "channel_values": {NOTE_KEY: NOTE_TEXT},
        "channel_versions": {},
        "versions_seen": {},
    }
    metadata = {
        "source": METADATA_SOURCE,
        "step": METADATA_STEP,
        "writes": {NOTE_KEY: NOTE_TEXT},
        "parents": {},
    }
    with open_postgres_saver() as saver:
        assert saver.serde.pickle_fallback is False
        saver.setup()
        try:
            saved = saver.put(write_config, checkpoint, metadata, {})
            assert saved[CONFIGURABLE_KEY][THREAD_ID_KEY] == conversation_id
            loaded = saver.get_tuple(thread_config(conversation_id))
            assert loaded is not None
            assert loaded.checkpoint["channel_values"][NOTE_KEY] == NOTE_TEXT
        finally:
            saver.delete_thread(conversation_id)


def _memory_call(query: str, index: int) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{
            "name": MEMORY_TOOL,
            "args": {"query": query},
            "id": f"{CALL_PREFIX}-{index}",
            "type": "tool_call",
        }],
    )


def _invoke(
    model: ScriptedChatModel,
    archive: MemoryArchive,
    thread_id: str,
) -> None:
    agent = build_web_agent(
        model,
        PinnedIndex(index_version=PINNED_VERSION),
        RecordingParents().search,
        RecordingWeb(),
        archive=archive,
    )
    agent.invoke(
        {"messages": [HumanMessage(content=QUESTION)]},
        {
            CONFIGURABLE_KEY: {THREAD_ID_KEY: thread_id},
            "recursion_limit": RECURSION_LIMIT,
        },
    )
