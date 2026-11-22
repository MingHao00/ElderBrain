"""网页 Agent 核心。假模型按脚本返回，检索和搜索都用注入的假实现。"""

import sys
from pathlib import Path

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field
from langgraph.checkpoint.memory import InMemorySaver
from typing_extensions import override

_RAG_DIR = Path(__file__).resolve().parent
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

from modules.data_preparation import RagPiece
from rag_search import ParentHit, PinnedIndex, SearchParents, rag_search
from web_tools import SEARCH_LIMIT, SearchClient

import web_agent
from web_agent import SYSTEM_PROMPT, TOOL_RUN_LIMIT, build_web_agent

RAG_TOOL = "rag_search"
WEB_TOOL = "web_search"
PINNED_VERSION = "pinned-2026-11-22"
QUESTION = "火球术怎么施放？"
RAG_QUERY = "火球术"
WEB_QUERY = "火球术 规则"
BLANK_QUERY = " \n\t"
FINAL_AFTER_BOTH = "火球术的伤害见父节原文。"
FINAL_AFTER_LIMIT = "次数用完后的正文。"
FINAL_AFTER_REPEAT = "同一种工具连查后的正文。"
FINAL_AFTER_BLANK = "空白查询已改用当前问题。"
ENTRY_ID = "spell-fireball"
ENTRY_TITLE = "火球术"
ENTRY_TEXT = "父节原文"
ENTRY_SCORE = "0.9"
PARENT_DOC_TYPE = "parent"
DOC_TYPE_FIELD = "doc_type"
ENTRY_ID_FIELD = "entry_id"
TITLE_FIELD = "title"
SCORE_FIELD = "relevance_score"
THREAD_BOTH = "thread-search-then-web"
THREAD_LIMIT = "thread-sixth-blocked"
THREAD_REPEAT = "thread-repeat-rag"
THREAD_BLANK = "thread-blank-query"
RECURSION_LIMIT = 40
EXPECTED_PROMPT = (
    "先检索已发布规则，只依据父节里的原文。"
    "库里没有就说明不是来自数据库，再搜索网页。"
    "工具合计最多 5 次。写出不含工具调用的正文后停止。"
)
BLOCKED_CALL_COUNT = TOOL_RUN_LIMIT + 1
QUERY_PREFIX = "q"
CALL_PREFIX = "call"
SOURCE_NAME = "web_agent.py"
FORBIDDEN_SOURCE = (
    "fetch_page",
    "TavilySearch",
    "TAVILY_API_KEY",
    "langchain_tavily",
    "PostgresSaver",
    "psycopg",
    "urlopen",
    "httpx",
    "requests",
    "socket",
    "InMemorySaver",
)


class ScriptedChatModel(BaseChatModel):
    """按给定消息依次返回。脚本用尽后抛错，避免循环调用真实模型。"""

    script: list[AIMessage]
    step: int = 0
    bound_names: list[str] = Field(default_factory=list)
    system_prompts: list[str] = Field(default_factory=list)

    @override
    def bind_tools(
        self,
        tools: object,
        *,
        tool_choice: str | None = None,
        **kwargs: object,
    ) -> "ScriptedChatModel":
        names: list[str] = []
        if isinstance(tools, list):
            names = [tool.name for tool in tools if hasattr(tool, "name")]
        self.bound_names = names
        return self

    @override
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object = None,
        **kwargs: object,
    ) -> ChatResult:
        self.system_prompts = [
            text
            for message in messages
            if isinstance(message, SystemMessage)
            for text in [_message_text(message)]
            if text
        ]
        if self.step >= len(self.script):
            raise RuntimeError("script exhausted")
        message = self.script[self.step]
        self.step += 1
        return ChatResult(generations=[ChatGeneration(message=message)])

    @property
    @override
    def _llm_type(self) -> str:
        return "scripted-web-agent"


class RecordingParents:
    """记下检索被调用时的查询词和钉住的版本。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def search(self, query: str, index_version: str) -> tuple[RagPiece, ...]:
        self.calls.append((query, index_version))
        return (_parent_piece(),)


class RecordingWeb:
    """记下搜索客户端收到的查询词和条数上限。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def search(
        self,
        query: str,
        max_results: int,
    ) -> dict[str, list[dict[str, str]]]:
        self.calls.append((query, max_results))
        return {"results": []}


def test_search_then_web_then_answer() -> None:
    """先检索、再搜索网页、最后写出正文。两个工具各执行一次。"""
    parents = RecordingParents()
    client = RecordingWeb()
    model = ScriptedChatModel(script=[
        _tool_message(RAG_TOOL, RAG_QUERY, 1),
        _tool_message(WEB_TOOL, WEB_QUERY, 2),
        AIMessage(content=FINAL_AFTER_BOTH),
    ])
    result = _run(model, parents, client, QUESTION, THREAD_BOTH)
    assert SYSTEM_PROMPT == EXPECTED_PROMPT
    assert EXPECTED_PROMPT in model.system_prompts
    assert model.bound_names == [RAG_TOOL, WEB_TOOL]
    assert parents.calls == [(RAG_QUERY, PINNED_VERSION)]
    assert client.calls == [(WEB_QUERY, SEARCH_LIMIT)]
    assert _final_text(result) == FINAL_AFTER_BOTH


def test_sixth_tool_is_not_executed_and_answer_remains() -> None:
    """准备调用 6 次工具。前 5 次执行，第 6 次函数不跑，最后仍有正文。"""
    parents = RecordingParents()
    client = RecordingWeb()
    queries = [_indexed_query(index) for index in range(1, BLOCKED_CALL_COUNT + 1)]
    script = [
        _tool_message(RAG_TOOL, query, index)
        for index, query in enumerate(queries, start=1)
    ]
    script.append(AIMessage(content=FINAL_AFTER_LIMIT))
    model = ScriptedChatModel(script=script)
    result = _run(model, parents, client, QUESTION, THREAD_LIMIT)
    executed = [query for query, _version in parents.calls]
    assert executed == queries[:TOOL_RUN_LIMIT]
    assert client.calls == []
    assert _final_text(result) == FINAL_AFTER_LIMIT


def test_repeated_rag_search_runs_until_the_shared_limit() -> None:
    """连续 5 次只调用检索。同一种工具不会提前停止。"""
    parents = RecordingParents()
    client = RecordingWeb()
    queries = [_indexed_query(index) for index in range(1, TOOL_RUN_LIMIT + 1)]
    script = [
        _tool_message(RAG_TOOL, query, index)
        for index, query in enumerate(queries, start=1)
    ]
    script.append(AIMessage(content=FINAL_AFTER_REPEAT))
    model = ScriptedChatModel(script=script)
    result = _run(model, parents, client, QUESTION, THREAD_REPEAT)
    assert [query for query, _version in parents.calls] == queries
    assert client.calls == []
    assert _final_text(result) == FINAL_AFTER_REPEAT


def test_blank_model_query_is_passed_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空白查询词原样交给 rag_search，由它改用当前问题。"""
    seen: dict[str, str] = {}

    def spy(
        pinned: PinnedIndex,
        search_parents: SearchParents,
        model_query: str,
        question: str,
    ) -> list[ParentHit]:
        seen["model_query"] = model_query
        seen["question"] = question
        return rag_search(pinned, search_parents, model_query, question)

    monkeypatch.setattr(web_agent.published_rules, "rag_search", spy)
    parents = RecordingParents()
    client = RecordingWeb()
    model = ScriptedChatModel(script=[
        _tool_message(RAG_TOOL, BLANK_QUERY, 1),
        AIMessage(content=FINAL_AFTER_BLANK),
    ])
    result = _run(model, parents, client, QUESTION, THREAD_BLANK)
    assert seen["model_query"] == BLANK_QUERY
    assert seen["question"] == QUESTION
    assert parents.calls == [(QUESTION, PINNED_VERSION)]
    assert _final_text(result) == FINAL_AFTER_BLANK


def test_agent_source_has_no_fetch_or_network() -> None:
    """这一步不装抓页，不读密钥，也不建立数据库或网络连接。"""
    source = Path(__file__).with_name(SOURCE_NAME).read_text(encoding="utf-8")
    for marker in FORBIDDEN_SOURCE:
        assert marker not in source


def _run(
    model: ScriptedChatModel,
    parents: RecordingParents,
    client: SearchClient,
    question: str,
    thread_id: str,
) -> dict[str, object]:
    saver = InMemorySaver()
    agent = build_web_agent(
        model,
        PinnedIndex(index_version=PINNED_VERSION),
        parents.search,
        client,
        checkpointer=saver,
    )
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": RECURSION_LIMIT,
    }
    result = agent.invoke(
        {"messages": [HumanMessage(content=question)]},
        config,
    )
    assert saver.get({"configurable": {"thread_id": thread_id}}) is not None
    return result


def _final_text(result: dict[str, object]) -> str:
    messages = result["messages"]
    assert isinstance(messages, list)
    last = messages[-1]
    assert isinstance(last, AIMessage)
    assert not last.tool_calls
    return _message_text(last)


def _tool_message(name: str, query: str, index: int) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{
            "name": name,
            "args": {"query": query},
            "id": f"{CALL_PREFIX}-{index}",
            "type": "tool_call",
        }],
    )


def _indexed_query(index: int) -> str:
    return f"{QUERY_PREFIX}{index}"


def _parent_piece() -> RagPiece:
    return RagPiece(
        text=ENTRY_TEXT,
        metadata={
            ENTRY_ID_FIELD: ENTRY_ID,
            DOC_TYPE_FIELD: PARENT_DOC_TYPE,
            TITLE_FIELD: ENTRY_TITLE,
            SCORE_FIELD: ENTRY_SCORE,
        },
    )


def _message_text(message: AIMessage | SystemMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return ""
