"""网页提问的 Agent 核心。

装上已有的规则检索、网页搜索和本对话记忆搜索。
检查点由调用方传入，本模块不连接数据库。
"""

from collections.abc import Sequence
from typing import Literal

from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.tools import ToolRuntime
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.tools import BaseTool, tool
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer

import agent_memory
import rag_search as published_rules
import web_tools

TOOL_RUN_LIMIT = 5
TOOL_LIMIT_EXIT: Literal["continue", "error", "end"] = "continue"
SYSTEM_PROMPT = (
    "先检索已发布规则，只依据父节里的原文。"
    "库里没有就说明不是来自数据库，再搜索网页。"
    "工具合计最多 5 次。写出不含工具调用的正文后停止。"
)
ENTRY_ID_FIELD = "entry_id"
TITLE_FIELD = "title"
TEXT_FIELD = "text"
SCORE_FIELD = "score"
MESSAGES_KEY = "messages"

ParentRow = dict[str, str | float]


def _latest_question(messages: Sequence[BaseMessage]) -> str:
    """取这一轮最近的用户原文，供空白查询回退。"""
    for message in reversed(messages):
        if isinstance(message, HumanMessage) and isinstance(message.content, str):
            return message.content
    return ""


def _parent_row(hit: published_rules.ParentHit) -> ParentRow:
    """父节只留条目、标题、正文和分数，方便写进工具消息。"""
    return {
        ENTRY_ID_FIELD: hit.entry_id,
        TITLE_FIELD: hit.title,
        TEXT_FIELD: hit.text,
        SCORE_FIELD: hit.score,
    }


def _rag_search_tool(
    pinned: published_rules.PinnedIndex,
    search_parents: published_rules.SearchParents,
) -> BaseTool:
    """包装已有检索。查询词原样传入，空白由 rag_search 改用当前问题。"""

    @tool
    def rag_search(query: str, runtime: ToolRuntime) -> list[ParentRow]:
        """检索已发布规则，只返回父节。"""
        question = _latest_question(runtime.state[MESSAGES_KEY])
        hits = published_rules.rag_search(
            pinned,
            search_parents,
            query,
            question,
        )
        return [_parent_row(hit) for hit in hits]

    return rag_search


def _web_search_tool(search_client: web_tools.SearchClient) -> BaseTool:
    """包装已有网页搜索。客户端由调用方注入，这里不创建连接。"""

    @tool
    def web_search(query: str) -> list[dict[str, str]]:
        """搜索网页。"""
        return web_tools.web_search(query, search_client)

    return web_search


def _call_limit() -> ToolCallLimitMiddleware:
    """一轮里的工具调用共用一个次数，同一种工具连叫也计入总额。"""
    return ToolCallLimitMiddleware(
        run_limit=TOOL_RUN_LIMIT,
        exit_behavior=TOOL_LIMIT_EXIT,
    )


def _thread_id(runtime: ToolRuntime) -> str:
    """当前工具调用的线程号。缺失时搜索不会落到别的对话。"""
    config = runtime.config
    if not isinstance(config, dict):
        return ""
    configurable = config.get(agent_memory.CONFIGURABLE_KEY)
    if not isinstance(configurable, dict):
        return ""
    thread_id = configurable.get(agent_memory.THREAD_ID_KEY, "")
    if isinstance(thread_id, str):
        return thread_id
    return ""


def _memory_search_tool(archive: agent_memory.MemoryArchive) -> BaseTool:
    """包装本对话记忆搜索。次数仍算进同一轮的工具上限。"""

    @tool
    def memory_search(query: str, runtime: ToolRuntime) -> list[str]:
        """搜索本对话里被压缩换下的原文。"""
        conversation_id = _thread_id(runtime)
        return agent_memory.memory_search(archive, conversation_id, query)

    return memory_search


def build_web_agent(
    model: BaseChatModel,
    pinned: published_rules.PinnedIndex,
    search_parents: published_rules.SearchParents,
    search_client: web_tools.SearchClient,
    *,
    checkpointer: Checkpointer = None,
    archive: agent_memory.MemoryArchive | None = None,
) -> CompiledStateGraph:
    """装上检索、网页搜索和记忆搜索，并限制一轮里的工具调用次数。

    Args:
        model: 对话模型。测试传入按脚本回答的假模型。
        pinned: 这一轮开始时已经钉住的已发布索引。
        search_parents: 按查询词和版本号返回父节的检索函数。
        search_client: 网页搜索客户端。本函数不创建网络客户端。
        checkpointer: 调用方传入的检查点。不传则不保存这一轮。
        archive: 被换下原文的存档。不传则使用空存档，工具仍在名单里。

    Returns:
        可 invoke 的 Agent 图。
    """
    store = archive if archive is not None else agent_memory.MemoryArchive()
    tools: list[BaseTool] = [
        _rag_search_tool(pinned, search_parents),
        _web_search_tool(search_client),
        _memory_search_tool(store),
    ]
    return create_agent(
        model,
        tools,
        system_prompt=SYSTEM_PROMPT,
        middleware=[_call_limit()],
        checkpointer=checkpointer,
    )
