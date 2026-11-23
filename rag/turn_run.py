"""一条对话的当前轮：暂停、继续、断开，以及丢掉暂停检查点。

检查点由调用方注入。本模块不连接数据库，也不发起网络请求。
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Protocol

from agent_memory import CONFIGURABLE_KEY, THREAD_ID_KEY, thread_config

ANSWER_NOT_COMMITTED = False
TOOL_JOIN_SECONDS = 2.0
EMPTY_INPUT = "不能为空"
CONVERSATION_LABEL = "对话号"
QUESTION_LABEL = "问题"
MISSING_CHECKPOINT = "没有可继续的暂停检查点"
TOOL_TIMEOUT = "工具没有在时限内返回"
PAUSE_NOT_ACCEPTED = "暂停没有开始"
TOOL_CONTENT_TYPE = "工具结果必须是字符串"
PARTIAL_TYPE = "半截正文必须是字符串"


@dataclass(frozen=True)
class ToolObservation:
    """一次工具调用的名字和结果。"""

    name: str
    content: str


@dataclass(frozen=True)
class TurnCheckpoint:
    """暂停后写下的检查点。这一步不会把答案标成已提交。"""

    thread_id: str
    turn_id: str
    partial_text: str
    user_messages: tuple[str, ...]
    tool_results: tuple[ToolObservation, ...]
    answer_committed: bool


@dataclass
class TurnQuotaState:
    """这一轮占了没有、费用和审计收口。不写入检查点，暂停形状保持不变。"""

    reserved: bool = False
    event_id: int | None = None
    has_body: bool = False
    cost_micro: int = 0
    audit_closed: bool = False


@dataclass(frozen=True)
class TurnState:
    """交给模型的当前轮。继续时沿用检查点里的用户消息和工具结果。"""

    user_messages: tuple[str, ...]
    partial_text: str
    tool_results: tuple[ToolObservation, ...]


@dataclass(frozen=True)
class ModelStep:
    """模型这一步的半截正文。tool_name 为空表示不调用工具。"""

    partial_text: str
    tool_name: str | None


class CheckpointStore(Protocol):
    """可替换的检查点存储。测试注入内存实现，生产以后再接数据库。"""

    def load(self, thread_id: str) -> TurnCheckpoint | None:
        """按线程号读取。没有则返回 None。"""
        ...

    def save(self, checkpoint: TurnCheckpoint) -> None:
        """写入。同一线程号覆盖上一份。"""
        ...

    def delete(self, thread_id: str) -> None:
        """删除这一线程的检查点。"""
        ...


class TurnModel(Protocol):
    """一步模型。由调用方注入，这里不创建客户端。"""

    def __call__(self, state: TurnState) -> ModelStep:
        """根据当前轮状态给出半截正文，以及要不要调用工具。"""
        ...


class TurnTool(Protocol):
    """一步工具。由调用方注入。"""

    def __call__(self, name: str) -> str:
        """执行名为 name 的工具并返回文本结果。"""
        ...


def _thread_id(conversation_id: str) -> str:
    """线程号等于对话号。"""
    config = thread_config(conversation_id)
    return config[CONFIGURABLE_KEY][THREAD_ID_KEY]


def _require_text(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}{EMPTY_INPUT}")


class TurnRun:
    """一条对话正在进行的那一轮。同一时刻只代表这一轮。"""

    def __init__(
        self,
        conversation_id: str,
        store: CheckpointStore,
        model: TurnModel,
        tool: TurnTool,
    ) -> None:
        """挂上对话号、检查点存储、模型和工具。

        Args:
            conversation_id: 对话号，同时作为线程号。
            store: 检查点存储。本对象不打开数据库连接。
            model: 一步模型。
            tool: 一步工具。
        """
        _require_text(conversation_id, CONVERSATION_LABEL)
        self._conversation_id = conversation_id
        self._store = store
        self._model = model
        self._tool = tool
        self._turn_id = ""
        self._partial = ""
        self._user_messages: list[str] = []
        self._tool_results: list[ToolObservation] = []
        self._pause_requested = threading.Event()
        self._pause_accepted = threading.Event()
        self._tool_done = threading.Event()
        self._cancelled = threading.Event()
        self._pause_processed = False
        self.quota = TurnQuotaState()

    @property
    def thread_id(self) -> str:
        """LangGraph 线程号，等于对话号。"""
        return _thread_id(self._conversation_id)

    @property
    def answer_committed(self) -> bool:
        """这一步从不提交答案。"""
        return ANSWER_NOT_COMMITTED

    @property
    def pause_requested(self) -> bool:
        """暂停信号已经到达，检查点可能还没写。"""
        return self._pause_requested.is_set()

    @property
    def pause_processed(self) -> bool:
        """检查点已经写完。工具返回之前一直为 False。"""
        return self._pause_processed

    def wait_until_pause_accepted(self) -> None:
        """等到暂停信号被接收。此时工具若还在跑，检查点尚未写入。

        Raises:
            TimeoutError: 时限内没有收到暂停。
        """
        if not self._pause_accepted.wait(TOOL_JOIN_SECONDS):
            raise TimeoutError(PAUSE_NOT_ACCEPTED)

    def start(self, question: str) -> None:
        """开始这一轮。先记下用户消息，再让模型决定要不要调用工具。

        Args:
            question: 这一轮的用户问题。

        Raises:
            ValueError: 问题为空。
        """
        _require_text(question, QUESTION_LABEL)
        self._turn_id = uuid.uuid4().hex
        self._user_messages.append(question)
        self._apply_model_step()

    def pause(self) -> TurnCheckpoint:
        """等正在跑的工具返回，再写入未提交的检查点。

        暂停可以在工具执行中到达。写检查点放在工具返回之后，
        半截正文和这次工具结果才会一起留下。

        Returns:
            刚写入的检查点。答案不会标成已提交。

        Raises:
            TimeoutError: 工具没有在时限内返回。
        """
        self._pause_requested.set()
        self._pause_accepted.set()
        self._wait_for_tool()
        return self._save()

    def resume(self) -> TurnCheckpoint:
        """从暂停检查点继续同一轮，不新增用户消息。

        Returns:
            继续之后的检查点。工具结果仍在，答案仍未提交。

        Raises:
            ValueError: 没有可继续的暂停检查点。
        """
        self._restore(self._load_paused())
        self._apply_model_step()
        return self._save()

    def disconnect(self) -> None:
        """取消这一轮。不写检查点，也不改已有的暂停检查点。"""
        self._cancelled.set()

    def discard_for_new_question(self, question: str) -> None:
        """暂停中收到新问题：删掉检查点。这里不调用模型。

        Args:
            question: 新问题。只校验非空，不送进模型。

        Raises:
            ValueError: 问题为空。
        """
        _require_text(question, QUESTION_LABEL)
        self._store.delete(self.thread_id)

    def _wait_for_tool(self) -> None:
        # 工具还没返回就不能写检查点，否则这次结果会丢掉。
        if not self._tool_done.wait(TOOL_JOIN_SECONDS):
            raise TimeoutError(TOOL_TIMEOUT)

    def _apply_model_step(self) -> None:
        if self._cancelled.is_set():
            return
        step = self._model(self._state())
        self._partial = _require_partial(step.partial_text)
        if self._cancelled.is_set() or not step.tool_name:
            return
        self._run_tool(step.tool_name)

    def _run_tool(self, name: str) -> None:
        try:
            content = self._tool(name)
            self._keep_tool_result(name, content)
        finally:
            self._tool_done.set()

    def _keep_tool_result(self, name: str, content: str) -> None:
        if self._cancelled.is_set():
            return
        if not isinstance(content, str):
            raise TypeError(TOOL_CONTENT_TYPE)
        self._tool_results.append(ToolObservation(name, content))

    def _load_paused(self) -> TurnCheckpoint:
        loaded = self._store.load(self.thread_id)
        if loaded is None:
            raise ValueError(MISSING_CHECKPOINT)
        return loaded

    def _restore(self, loaded: TurnCheckpoint) -> None:
        self._turn_id = loaded.turn_id
        self._partial = loaded.partial_text
        self._user_messages = list(loaded.user_messages)
        self._tool_results = list(loaded.tool_results)

    def _state(self) -> TurnState:
        return TurnState(
            user_messages=tuple(self._user_messages),
            partial_text=self._partial,
            tool_results=tuple(self._tool_results),
        )

    def _save(self) -> TurnCheckpoint:
        checkpoint = TurnCheckpoint(
            thread_id=self.thread_id,
            turn_id=self._turn_id,
            partial_text=self._partial,
            user_messages=tuple(self._user_messages),
            tool_results=tuple(self._tool_results),
            answer_committed=ANSWER_NOT_COMMITTED,
        )
        self._store.save(checkpoint)
        self._pause_processed = True
        return checkpoint


def _require_partial(partial_text: str) -> str:
    if not isinstance(partial_text, str):
        raise TypeError(PARTIAL_TYPE)
    return partial_text
