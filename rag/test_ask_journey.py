"""一条旅程：登录后提问、暂停、继续，刷新后未提交的这轮消失。"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

_RAG_DIR = Path(__file__).resolve().parent
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

os.environ["PLAYWRIGHT_BROWSERS_PATH"] = r"E:\ProgramData\playwright-browsers"

import uvicorn
from auth_app import AuthApp
from auth_constants import CODE_LENGTH
from auth_db import AuthDb
from auth_testkit import auth_env
from playwright.sync_api import Page, expect, sync_playwright
from turn_run import ModelStep, TurnState
from web_app import build_web_app

HOST = "127.0.0.1"
EMAIL = "journey@example.com"
QUESTION = "火球术射程是多少"
FIRST_TEXT = "火球术射程见原文。"
LONGER_TEXT = FIRST_TEXT + "补充后的射程仍见原文。"
TOOL_NAME = "lookup"
PAUSE_NAME = "暂停"
CONTINUE_NAME = "Continue Working"
UI_WAIT_SECONDS = 10.0
TOOL_WAIT_SECONDS = 30.0
POLL_SECONDS = 0.05
JOIN_SECONDS = 5.0
SESSION_SUFFIX = "/api/auth/session"
LIST_SUFFIX = "/api/conversations"


class CodeMail:
    """只记下验证码，不连接 SMTP。"""

    def __init__(self) -> None:
        self._codes: dict[str, str] = {}

    def __call__(self, recipient: str, code: str) -> None:
        self._codes[recipient] = code

    def ready(self) -> bool:
        """替身里已经有一条验证码。"""
        return len(self._codes) == 1

    def only(self) -> str:
        """取出唯一的验证码。长度必须是登录常量。"""
        if not self.ready():
            raise AssertionError("验证码没有只记下一条")
        code = next(iter(self._codes.values()))
        if len(code) != CODE_LENGTH:
            raise AssertionError("验证码长度不对")
        return code


class IdleRag:
    """注入了模型就不会走到这里。"""

    def ask(self, question: str, card: object) -> str:
        del question, card
        raise AssertionError("不应该调用真实回答")


class ScriptModel:
    """按脚本返回半截正文。用尽后不再给出结束。"""

    def __init__(self, steps: tuple[ModelStep, ...]) -> None:
        self._steps = steps
        self.seen: list[TurnState] = []

    def __call__(self, state: TurnState) -> ModelStep:
        index = len(self.seen)
        self.seen.append(state)
        if index >= len(self._steps):
            raise AssertionError("这轮不该结束")
        return self._steps[index]


class GateTool:
    """每次调用都停住，直到测试放行这一次。"""

    def __init__(self) -> None:
        self.calls = 0
        self.second_started = threading.Event()
        self.release_first = threading.Event()
        self.release_second = threading.Event()
        self._lock = threading.Lock()

    def __call__(self, name: str) -> str:
        del name
        call = self._claim()
        if call == 1:
            self._wait(self.release_first, "第一把工具没有放行")
            return "held-first"
        self.second_started.set()
        self._wait(self.release_second, "第二把工具没有放行")
        return "held-second"

    def release(self) -> None:
        """收尾时放行，避免服务线程一直停着。"""
        self.release_first.set()
        self.release_second.set()

    def _claim(self) -> int:
        with self._lock:
            self.calls += 1
            return self.calls

    def _wait(self, gate: threading.Event, message: str) -> None:
        if not gate.wait(TOOL_WAIT_SECONDS):
            raise TimeoutError(message)


class _Server(uvicorn.Server):
    def install_signal_handlers(self) -> None:
        """后台线程里不注册信号。"""
        return None


def test_logged_in_turn_vanishes_on_refresh() -> None:
    """登录后看到正文、暂停、继续，刷新进行中的页面后这一轮消失。"""
    mail = CodeMail()
    tool = GateTool()
    model = ScriptModel((
        ModelStep(FIRST_TEXT, TOOL_NAME),
        ModelStep(LONGER_TEXT, TOOL_NAME),
    ))
    with _served(mail, model, tool) as origin:
        _browse(origin, mail, tool)


@contextmanager
def _served(
    mail: CodeMail,
    model: ScriptModel,
    tool: GateTool,
) -> Iterator[str]:
    env = auth_env()
    env.__enter__()
    folder = tempfile.TemporaryDirectory()
    server: _Server | None = None
    thread: threading.Thread | None = None
    try:
        origin, server, thread = _listen(folder.name, mail, model, tool)
        yield origin
    finally:
        tool.release()
        _stop(server, thread)
        folder.cleanup()
        env.__exit__(None, None, None)


def _listen(
    folder: str,
    mail: CodeMail,
    model: ScriptModel,
    tool: GateTool,
) -> tuple[str, _Server, threading.Thread]:
    db = AuthDb(Path(folder) / "auth.sqlite")
    app = build_web_app(IdleRag(), AuthApp(db, mail, None), model, tool)
    config = uvicorn.Config(
        app,
        host=HOST,
        port=0,
        log_level="warning",
        access_log=False,
    )
    server = _Server(config)
    thread = threading.Thread(target=server.run, name="ask-journey")
    thread.start()
    _wait_until(lambda: server.started, "服务没有起来")
    return _origin(server), server, thread


def _origin(server: _Server) -> str:
    sockets = server.servers[0].sockets
    if not sockets:
        raise RuntimeError("服务没有监听端口")
    port = sockets[0].getsockname()[1]
    if not isinstance(port, int):
        raise RuntimeError("服务没有监听端口")
    return f"http://{HOST}:{port}"


def _stop(server: _Server | None, thread: threading.Thread | None) -> None:
    if server is not None:
        server.should_exit = True
    if thread is not None:
        thread.join(JOIN_SECONDS)


def _wait_until(ready: Callable[[], bool], message: str) -> None:
    deadline = time.monotonic() + UI_WAIT_SECONDS
    while not ready():
        if time.monotonic() >= deadline:
            raise TimeoutError(message)
        time.sleep(POLL_SECONDS)


def _browse(origin: str, mail: CodeMail, tool: GateTool) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(int(UI_WAIT_SECONDS * 1000))
        try:
            _login(page, origin, mail)
            _pause_after_first_text(page)
            _continue_then_refresh(page, tool)
        finally:
            browser.close()


def _login(page: Page, origin: str, mail: CodeMail) -> None:
    with page.expect_response(_is_session) as caught:
        page.goto(origin + "/", wait_until="domcontentloaded")
    assert caught.value.ok
    page.locator("#loginEmail").fill(EMAIL)
    page.locator("#sendCodeButton").click()
    page.locator("#loginCode").fill(_read_code(page, mail))
    page.locator("#verifyCodeButton").click()
    expect(page.locator("#loggedIn")).to_be_visible()


def _read_code(page: Page, mail: CodeMail) -> str:
    deadline = time.monotonic() + UI_WAIT_SECONDS
    while not mail.ready():
        if time.monotonic() >= deadline:
            raise TimeoutError("验证码没有记下")
        page.wait_for_timeout(int(POLL_SECONDS * 1000))
    return mail.only()


def _pause_after_first_text(page: Page) -> None:
    page.locator("#messageInput").fill(QUESTION)
    page.locator("#sendButton").click()
    expect(page.locator("#messagesContainer")).to_contain_text(FIRST_TEXT)
    expect(page.locator("#sendButton")).to_have_attribute("aria-label", PAUSE_NAME)
    page.locator("#sendButton").click()
    expect(page.locator("#continueButton")).to_be_visible()
    expect(page.locator("#continueButton")).to_have_text(CONTINUE_NAME)
    expect(page.locator("#messagesContainer")).to_contain_text(FIRST_TEXT)


def _continue_then_refresh(page: Page, tool: GateTool) -> None:
    page.locator("#continueButton").click()
    tool.release_first.set()
    expect(page.locator("#messagesContainer")).to_contain_text(LONGER_TEXT)
    if not tool.second_started.wait(UI_WAIT_SECONDS):
        raise TimeoutError("继续后的工具没有停住")
    _refresh_drops_turn(page)


def _refresh_drops_turn(page: Page) -> None:
    with page.expect_response(_is_conversation_list) as caught:
        page.reload(wait_until="networkidle")
    assert caught.value.ok
    expect(page.locator("#loggedIn")).to_be_visible()
    text = page.locator("#messagesContainer").inner_text()
    assert LONGER_TEXT not in text
    assert FIRST_TEXT not in text
    assert page.locator("#continueButton").is_hidden()


def _is_session(response: object) -> bool:
    url = getattr(response, "url", "")
    return isinstance(url, str) and url.endswith(SESSION_SUFFIX)


def _is_conversation_list(response: object) -> bool:
    url = getattr(response, "url", "")
    request = getattr(response, "request", None)
    method = getattr(request, "method", "")
    return (
        isinstance(url, str)
        and url.rstrip("/").endswith(LIST_SUFFIX)
        and method == "GET"
    )
