"""断言当前进程可以导入 LangChain 的 create_agent。"""

from collections.abc import Callable
from typing import Any


def test_create_agent_is_importable_and_callable() -> None:
    """create_agent 必须能从 langchain.agents 导入，并且可以调用。"""
    from langchain.agents import create_agent

    agent_factory: Callable[..., Any] = create_agent
    assert callable(agent_factory)
