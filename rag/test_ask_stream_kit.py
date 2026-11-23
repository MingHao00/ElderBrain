"""提问流测试的替身。不连接模型，也不读取联网密钥。"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_RAG_DIR = Path(__file__).resolve().parent
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

from auth_app import AuthApp
from auth_db import Account, AuthDb
from auth_identity import hash_ip, ip_salt
from auth_session import issue_for, now_seconds
from auth_testkit import auth_env, cookie_header, json_bytes
from conversation_store import commit_turn
from fastapi.testclient import TestClient
from turn_run import ModelStep, TurnState

CLIENT_HOST = "testclient"
CSRF_TOKEN = "csrf-token-for-ask-stream01"
WAIT_SECONDS = 2.0
TOOL_STUCK = "假工具没有被放行"
STREAM_STUCK = "提问流没有结束"
PRIOR_QUESTION = "已经提交的问题"
PRIOR_ANSWER = "已经提交的回答"
QUESTION = "火球术怎么施放"
SNEAKY_QUESTION = "不该当成新问题"
FIRST_TEXT = "可见正文"
SUFFIX_TEXT = "已经写完"
FINAL_TEXT = FIRST_TEXT + SUFFIX_TEXT
TOOL_NAME = "hidden_tool"
TOOL_PARAM = "hidden-param"
HOLD_RESULT = "hidden-result"


class IdleRag:
    """活路径不应碰它。碰到就说明又去建了真实回答。"""

    def __init__(self) -> None:
        self.calls = 0

    def ask(self, question: str, card: object) -> str:
        del question, card
        self.calls += 1
        raise AssertionError("不应该调用真实回答")


class ScriptModel:
    """按脚本返回半截正文。测试用它固定事件顺序。"""

    def __init__(self, steps: list[ModelStep]) -> None:
        self._steps = list(steps)
        self.seen: list[TurnState] = []

    def __call__(self, state: TurnState) -> ModelStep:
        index = len(self.seen)
        self.seen.append(state)
        if index >= len(self._steps):
            raise AssertionError("模型步骤用尽")
        return self._steps[index]


class HoldTool:
    """开始后停住，直到测试放行。返回值里带着不该进流的结果。"""

    def __init__(self, result: str) -> None:
        self._result = result
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def __call__(self, name: str) -> str:
        del name
        self.calls += 1
        self.started.set()
        if not self.release.wait(WAIT_SECONDS):
            raise TimeoutError(TOOL_STUCK)
        return self._result


class ReadyTool:
    """立刻返回。结果仍不该出现在事件里。"""

    def __init__(self, result: str) -> None:
        self._result = result
        self.calls = 0

    def __call__(self, name: str) -> str:
        del name
        self.calls += 1
        return self._result


@contextmanager
def opened_auth() -> Iterator[AuthApp]:
    env = auth_env()
    env.__enter__()
    folder = tempfile.TemporaryDirectory()
    try:
        db = AuthDb(Path(folder.name) / "auth.sqlite")
        yield AuthApp(db, lambda _recipient, _code: None, None)
    finally:
        folder.cleanup()
        env.__exit__(None, None, None)


def session_headers(auth: AuthApp, email: str) -> tuple[Account, dict[str, str]]:
    salt = ip_salt()
    assert salt is not None
    account = auth.db.login_email(email, hash_ip(CLIENT_HOST, salt), now_seconds())
    assert isinstance(account, Account)
    issued = issue_for(account, now_seconds())
    assert issued is not None
    token, _role = issued
    headers = {
        "cookie": cookie_header(token, CSRF_TOKEN),
        "x-csrf-token": CSRF_TOKEN,
    }
    return account, headers


def seed_conversation(auth: AuthApp, user_id: str) -> str:
    return commit_turn(
        auth.db,
        user_id,
        None,
        PRIOR_QUESTION,
        PRIOR_ANSWER,
        None,
        now_seconds(),
    )


def parse_sse(raw: bytes) -> list[tuple[str, dict[str, object]]]:
    """按事件块读 SSE。注释行跳过。"""
    found: list[tuple[str, dict[str, object]]] = []
    for block in raw.decode("utf-8").split("\n\n"):
        event_name, payload = _sse_block(block)
        if event_name is None or payload is None:
            continue
        found.append((event_name, payload))
    return found


class OpenStream:
    """后台读流。主线程可以在工具停住时发暂停或第二次提问。"""

    def __init__(
        self,
        client: TestClient,
        path: str,
        body: dict[str, object],
        headers: dict[str, str],
    ) -> None:
        self._sink: dict[str, object] = {}
        self._worker = threading.Thread(
            target=self._run,
            args=(client, path, body, headers),
        )

    def start(self) -> None:
        self._worker.start()

    def finish(self) -> tuple[int, str, bytes]:
        self._worker.join(WAIT_SECONDS)
        if self._worker.is_alive():
            raise TimeoutError(STREAM_STUCK)
        status = self._sink.get("status")
        kind = self._sink.get("kind")
        payload = self._sink.get("body")
        if not isinstance(status, int) or not isinstance(kind, str):
            raise TimeoutError(STREAM_STUCK)
        if not isinstance(payload, bytes):
            raise TimeoutError(STREAM_STUCK)
        return status, kind, payload

    def _run(
        self,
        client: TestClient,
        path: str,
        body: dict[str, object],
        headers: dict[str, str],
    ) -> None:
        response = client.post(path, content=json_bytes(body), headers=headers)
        self._sink["status"] = response.status_code
        self._sink["kind"] = response.headers.get("content-type", "")
        self._sink["body"] = response.content


def _sse_block(block: str) -> tuple[str | None, dict[str, object] | None]:
    name: str | None = None
    data: str | None = None
    for line in block.split("\n"):
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data = line.split(":", 1)[1].strip()
    if name is None or data is None:
        return None, None
    payload = json.loads(data)
    if not isinstance(payload, dict):
        return name, None
    return name, payload
