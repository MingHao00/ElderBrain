"""进程内一轮提问的门闩和暂停检查点。不连接数据库，也不创建模型。"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass

from agent_memory import CONFIGURABLE_KEY, THREAD_ID_KEY, thread_config
from auth_app import AuthApp
from auth_constants import JSON_TYPE
from fastapi import Request
from fastapi.responses import Response
from turn_run import CheckpointStore, ToolObservation, TurnCheckpoint, TurnModel, TurnTool

EVENT_DELTA = "delta"
EVENT_REPLACE = "replace"
EVENT_PAUSED = "paused"
EVENT_DONE = "done"
EVENT_ERROR = "error"
LEGACY_VERSION = "version"
LEGACY_SENTENCE = "sentence"
LEGACY_DONE = "done"
POLL_SECONDS = 0.02


class AskDenied(Exception):
    """流开始前的 HTTP 拒绝。状态和文案原样返回。"""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class AskReady:
    """一轮已经通过流开始前的检查。"""

    held_id: str
    conversation_id: str
    user_id: str
    legacy_body: bytes | None
    visible: str
    user_messages: tuple[str, ...]
    tool_results: tuple[ToolObservation, ...]
    turn_id: str
    auth: AuthApp
    runtime: AskRuntime


class MemoryCheckpointStore:
    """测试和生产这一步共用的进程内检查点。"""

    def __init__(self) -> None:
        self._saved: dict[str, TurnCheckpoint] = {}
        self._lock = threading.Lock()

    def load(self, thread_id: str) -> TurnCheckpoint | None:
        """按线程号读取。没有则返回 None。"""
        with self._lock:
            return self._saved.get(thread_id)

    def save(self, checkpoint: TurnCheckpoint) -> None:
        """同一线程号覆盖上一份。"""
        with self._lock:
            self._saved[checkpoint.thread_id] = checkpoint

    def delete(self, thread_id: str) -> None:
        """删掉这一线程的检查点。"""
        with self._lock:
            self._saved.pop(thread_id, None)


class TurnGate:
    """同一条对话同时只允许一轮。暂停信号挂在这一轮上。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: dict[str, threading.Event] = {}

    def begin(self, conversation_id: str) -> bool:
        """占住这一轮。已经有人在跑就返回 False。"""
        with self._lock:
            if conversation_id in self._running:
                return False
            self._running[conversation_id] = threading.Event()
            return True

    def end(self, conversation_id: str) -> None:
        """轮次结束、暂停或出错后放开。重复调用无事。"""
        if not conversation_id:
            return
        with self._lock:
            self._running.pop(conversation_id, None)

    def request_pause(self, conversation_id: str) -> bool:
        """给正在跑的那一轮发暂停。没有在跑就返回 False。"""
        with self._lock:
            event = self._running.get(conversation_id)
            if event is None:
                return False
            event.set()
            return True

    def pause_event(self, conversation_id: str) -> threading.Event | None:
        """正在跑时返回暂停信号，供流里轮询。"""
        with self._lock:
            return self._running.get(conversation_id)


class AskRuntime:
    """挂在应用上的模型、工具、检查点和门闩。模型可以不装。"""

    def __init__(
        self,
        model: TurnModel | None,
        tool: TurnTool | None,
        store: CheckpointStore | None = None,
    ) -> None:
        self.model = model
        self.tool = tool
        self.store: CheckpointStore = store or MemoryCheckpointStore()
        self.gate = TurnGate()


def thread_of(conversation_id: str) -> str:
    """线程号等于对话号。"""
    config = thread_config(conversation_id)
    return config[CONFIGURABLE_KEY][THREAD_ID_KEY]


def denied_response(_request: Request, exc: AskDenied) -> Response:
    """拒绝正文沿用现有的 error 字段，不打开 SSE。"""
    body = json.dumps({"error": exc.message}, ensure_ascii=False).encode("utf-8")
    response = Response(content=body, status_code=exc.status)
    response.headers["content-type"] = JSON_TYPE
    return response
