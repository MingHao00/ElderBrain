"""提问、暂停、继续的 SSE。假模型和假工具由测试注入。"""

from __future__ import annotations

import sys
from pathlib import Path

_RAG_DIR = Path(__file__).resolve().parent
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

from auth_constants import (
    EMPTY_QUESTION,
    ERR_FORBIDDEN,
    ERR_TURN_RUNNING,
    HTTP_CONFLICT,
    HTTP_FORBIDDEN,
    HTTP_OK,
    HTTP_UNPROCESSABLE,
    PATH_ASK,
    PATH_ASK_CONTINUE,
    PATH_ASK_PAUSE,
    PATH_CONVERSATIONS,
)
from fastapi.testclient import TestClient
from test_ask_stream_kit import (
    FIRST_TEXT,
    FINAL_TEXT,
    HOLD_RESULT,
    QUESTION,
    SNEAKY_QUESTION,
    SUFFIX_TEXT,
    TOOL_NAME,
    TOOL_PARAM,
    HoldTool,
    IdleRag,
    OpenStream,
    ReadyTool,
    ScriptModel,
    opened_auth,
    parse_sse,
    seed_conversation,
    session_headers,
)
from turn_run import ModelStep, TurnCheckpoint
from web_app import build_web_app

PRIOR_MESSAGES = 2


def test_round_streams_delta_replace_and_done_without_tool_bits() -> None:
    """正文先流出，变成工具调用时替换，工具返回后再补一段并结束。"""
    steps = [
        ModelStep(FIRST_TEXT, f"{TOOL_NAME} {TOOL_PARAM}"),
        ModelStep(FINAL_TEXT, None),
    ]
    model = ScriptModel(steps)
    tool = ReadyTool(HOLD_RESULT)
    rag = IdleRag()
    with opened_auth() as auth:
        _account, headers = session_headers(auth, "ask-stream@example.com")
        app = build_web_app(rag, auth, model, tool)
        with TestClient(app) as client:
            response = client.post(
                PATH_ASK,
                content=_body({"question": QUESTION}),
                headers=headers,
            )
    events = parse_sse(response.content)
    blob = response.content.decode("utf-8")
    assert response.status_code == HTTP_OK
    assert "text/event-stream" in response.headers["content-type"]
    assert [name for name, _payload in events] == ["delta", "replace", "delta", "done"]
    assert events[0][1] == {"text": FIRST_TEXT}
    assert events[1][1] == {"text": FIRST_TEXT}
    assert events[2][1] == {"text": SUFFIX_TEXT}
    assert events[3][1] == {"text": FINAL_TEXT}
    assert TOOL_NAME not in blob
    assert TOOL_PARAM not in blob
    assert HOLD_RESULT not in blob
    assert rag.calls == 0
    assert tool.calls == 1


def test_empty_question_and_missing_id_stay_http() -> None:
    """空问题、暂停和继续缺对话号都在流开始前拒绝。"""
    with opened_auth() as auth:
        _account, headers = session_headers(auth, "ask-empty@example.com")
        app = build_web_app(IdleRag(), auth, ScriptModel([]), ReadyTool(HOLD_RESULT))
        with TestClient(app) as client:
            empty = client.post(
                PATH_ASK,
                content=_body({"question": "  "}),
                headers=headers,
            )
            missing_pause = client.post(PATH_ASK_PAUSE, content=_body({}), headers=headers)
            missing_continue = client.post(
                PATH_ASK_CONTINUE,
                content=_body({"question": SNEAKY_QUESTION}),
                headers=headers,
            )
    assert empty.status_code == HTTP_UNPROCESSABLE
    assert empty.json()["error"] == EMPTY_QUESTION
    assert "event-stream" not in empty.headers["content-type"]
    assert missing_pause.status_code == HTTP_UNPROCESSABLE
    assert missing_continue.status_code == HTTP_UNPROCESSABLE
    assert "event-stream" not in missing_pause.headers["content-type"]
    assert "event-stream" not in missing_continue.headers["content-type"]


def test_other_user_cannot_ask_pause_continue_or_read_partial() -> None:
    """别人的对话号不能提问、暂停、继续，GET 里也没有半截正文。"""
    with opened_auth() as auth:
        owner, headers = session_headers(auth, "ask-owner@example.com")
        _other, foreign = session_headers(auth, "ask-other@example.com")
        conversation_id = seed_conversation(auth, owner.user_id)
        app = build_web_app(IdleRag(), auth, ScriptModel([]), ReadyTool(HOLD_RESULT))
        _plant_partial(app, conversation_id)
        with TestClient(app) as client:
            asked = client.post(
                PATH_ASK,
                content=_body({"question": QUESTION, "conversation_id": conversation_id}),
                headers=foreign,
            )
            paused = client.post(
                PATH_ASK_PAUSE,
                content=_body({"conversation_id": conversation_id}),
                headers=foreign,
            )
            continued = client.post(
                PATH_ASK_CONTINUE,
                content=_body({"conversation_id": conversation_id}),
                headers=foreign,
            )
            hidden = client.get(_conversation_path(conversation_id), headers=foreign)
            visible = client.get(_conversation_path(conversation_id), headers=headers)
    assert asked.status_code == HTTP_FORBIDDEN
    assert asked.json()["error"] == ERR_FORBIDDEN
    assert "event-stream" not in asked.headers["content-type"]
    assert paused.status_code == HTTP_FORBIDDEN
    assert paused.json()["error"] == ERR_FORBIDDEN
    assert continued.status_code == HTTP_FORBIDDEN
    assert continued.json()["error"] == ERR_FORBIDDEN
    assert hidden.status_code == HTTP_FORBIDDEN
    assert FIRST_TEXT not in hidden.text
    assert visible.status_code == HTTP_OK
    assert visible.json()["paused"] is True
    assert visible.json()["partial"] == FIRST_TEXT
    assert len(visible.json()["messages"]) == PRIOR_MESSAGES
    assert all(item["text"] != FIRST_TEXT for item in visible.json()["messages"])


def test_running_turn_rejects_a_second_ask() -> None:
    """同一条对话有一轮未结束时，再提问得到 409，不开第二条流。"""
    model = ScriptModel([
        ModelStep(FIRST_TEXT, TOOL_NAME),
        ModelStep(FINAL_TEXT, None),
        ModelStep(FINAL_TEXT, None),
    ])
    tool = HoldTool(HOLD_RESULT)
    with opened_auth() as auth:
        owner, headers = session_headers(auth, "ask-busy@example.com")
        conversation_id = seed_conversation(auth, owner.user_id)
        app = build_web_app(IdleRag(), auth, model, tool)
        with TestClient(app) as client:
            stream = OpenStream(client, PATH_ASK, _ask_body(conversation_id), headers)
            stream.start()
            assert tool.started.wait(2)
            again = client.post(
                PATH_ASK,
                content=_body(_ask_body(conversation_id)),
                headers=headers,
            )
            tool.release.set()
            status, kind, body = stream.finish()
            later = client.post(
                PATH_ASK,
                content=_body(_ask_body(conversation_id)),
                headers=headers,
            )
    assert again.status_code == HTTP_CONFLICT
    assert again.json()["error"] == ERR_TURN_RUNNING
    assert "event-stream" not in again.headers["content-type"]
    assert status == HTTP_OK
    assert "text/event-stream" in kind
    assert [name for name, _payload in parse_sse(body)][-1] == "done"
    assert later.status_code == HTTP_OK
    assert "text/event-stream" in later.headers["content-type"]


def test_pause_before_tool_returns_then_get_and_continue() -> None:
    """工具还没返回就暂停。GET 看到同一段 partial，继续不把正文当新问题。"""
    model = ScriptModel([
        ModelStep(FIRST_TEXT, f"{TOOL_NAME} {TOOL_PARAM}"),
        ModelStep(FINAL_TEXT, None),
    ])
    tool = HoldTool(HOLD_RESULT)
    with opened_auth() as auth:
        owner, headers = session_headers(auth, "ask-pause@example.com")
        conversation_id = seed_conversation(auth, owner.user_id)
        app = build_web_app(IdleRag(), auth, model, tool)
        with TestClient(app) as client:
            before = client.get(_conversation_path(conversation_id), headers=headers)
            stream = OpenStream(client, PATH_ASK, _ask_body(conversation_id), headers)
            stream.start()
            assert tool.started.wait(2)
            try:
                paused = client.post(
                    PATH_ASK_PAUSE,
                    content=_body({"conversation_id": conversation_id}),
                    headers=headers,
                )
                status, kind, body = stream.finish()
            finally:
                tool.release.set()
            opened = client.get(_conversation_path(conversation_id), headers=headers)
            continued = client.post(
                PATH_ASK_CONTINUE,
                content=_body({
                    "conversation_id": conversation_id,
                    "question": SNEAKY_QUESTION,
                }),
                headers=headers,
            )
            finished = client.get(_conversation_path(conversation_id), headers=headers)
    _assert_paused(before, paused, status, kind, body, opened)
    _assert_continued(model, continued, finished)


def _assert_paused(
    before: object,
    paused: object,
    status: int,
    kind: str,
    body: bytes,
    opened: object,
) -> None:
    events = parse_sse(body)
    blob = body.decode("utf-8")
    assert "paused" not in before.json()
    assert "partial" not in before.json()
    assert paused.status_code == HTTP_OK
    assert status == HTTP_OK
    assert "text/event-stream" in kind
    assert [name for name, _payload in events] == ["delta", "replace", "paused"]
    assert events[0][1] == {"text": FIRST_TEXT}
    assert events[1][1] == {"text": FIRST_TEXT}
    assert events[2][1] == {"partial": FIRST_TEXT}
    assert TOOL_NAME not in blob
    assert TOOL_PARAM not in blob
    assert HOLD_RESULT not in blob
    assert opened.json()["paused"] is True
    assert opened.json()["partial"] == FIRST_TEXT
    assert len(opened.json()["messages"]) == len(before.json()["messages"])
    assert all(item["text"] != FIRST_TEXT for item in opened.json()["messages"])


def _assert_continued(model: ScriptModel, continued: object, finished: object) -> None:
    continued_events = parse_sse(continued.content)
    blob = continued.content.decode("utf-8")
    assert continued.status_code == HTTP_OK
    assert "text/event-stream" in continued.headers["content-type"]
    assert continued_events[-1] == ("done", {"text": FINAL_TEXT})
    assert model.seen[-1].user_messages == (QUESTION,)
    assert SNEAKY_QUESTION not in model.seen[-1].user_messages
    assert TOOL_NAME not in blob
    assert TOOL_PARAM not in blob
    assert HOLD_RESULT not in blob
    assert "paused" not in finished.json()
    assert "partial" not in finished.json()
    texts = [item["text"] for item in finished.json()["messages"]]
    assert FINAL_TEXT in texts
    assert SNEAKY_QUESTION not in texts


def _body(payload: dict[str, object]) -> bytes:
    from auth_testkit import json_bytes

    return json_bytes(payload)


def _ask_body(conversation_id: str) -> dict[str, object]:
    return {"question": QUESTION, "conversation_id": conversation_id}


def _conversation_path(conversation_id: str) -> str:
    return PATH_CONVERSATIONS + "/" + conversation_id


def _plant_partial(app: object, conversation_id: str) -> None:
    from agent_memory import CONFIGURABLE_KEY, THREAD_ID_KEY, thread_config

    config = thread_config(conversation_id)
    store = app.state.runtime.store
    store.save(TurnCheckpoint(
        thread_id=config[CONFIGURABLE_KEY][THREAD_ID_KEY],
        turn_id="planted-turn",
        partial_text=FIRST_TEXT,
        user_messages=(QUESTION,),
        tool_results=(),
        answer_committed=False,
    ))
