"""测试夹具。临时库，不连网。"""

from __future__ import annotations

import base64
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping

from auth_constants import CSRF_COOKIE, JWT_KEY_BYTES, SESSION_COOKIE
from auth_http import Incoming, Outgoing

SIGNING_KEY = base64.b64encode(b"k" * JWT_KEY_BYTES).decode("ascii")
TEST_SALT = "test-salt-value"
TEST_IP = "203.0.113.8"


@contextmanager
def auth_env(extra: Mapping[str, str | None] | None = None) -> Iterator[None]:
    """临时写入登录环境变量，结束时恢复。"""
    updates: dict[str, str | None] = {
        "JWT_SIGNING_KEY": SIGNING_KEY,
        "LOGIN_IP_SALT": TEST_SALT,
        "ADMIN_IDENTITIES": "",
    }
    if extra:
        updates.update(extra)
    saved = {key: os.environ.get(key) for key in updates}
    _apply(updates)
    try:
        yield
    finally:
        _apply(saved)


def make_request(
    method: str,
    path: str,
    body: bytes = b"",
    headers: Mapping[str, str] | None = None,
    query: Mapping[str, str] | None = None,
    client_ip: str = TEST_IP,
) -> Incoming:
    """组一个不经过套接字的请求。"""
    raw = headers or {}
    return Incoming(
        method,
        path,
        query or {},
        {key.lower(): value for key, value in raw.items()},
        body,
        client_ip,
    )


def json_bytes(payload: dict[str, object]) -> bytes:
    """测试请求的 JSON 正文。"""
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def read_body(outgoing: Outgoing) -> dict[str, object]:
    """解析 JSON 响应。"""
    payload = json.loads(outgoing.body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError("响应不是对象")
    return payload


def read_events(outgoing: Outgoing) -> list[dict[str, object]]:
    """解析网页提问的逐行 JSON。"""
    events: list[dict[str, object]] = []
    for line in outgoing.body.decode("utf-8").split("\n"):
        if not line:
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise AssertionError("事件不是对象")
        events.append(item)
    return events


def event_text(outgoing: Outgoing) -> str:
    """把版本句和已经推出的句子拼起来，便于旧断言查看正文。"""
    parts = [
        str(item.get("text", ""))
        for item in read_events(outgoing)
        if item.get("event") in {"version", "sentence"}
    ]
    return "\n".join(parts)


def set_cookie(outgoing: Outgoing, name: str) -> str:
    """从 Set-Cookie 取出一个值。"""
    prefix = name + "="
    for key, value in outgoing.headers:
        if key != "Set-Cookie":
            continue
        pair = value.split(";", 1)[0]
        if pair.startswith(prefix):
            return pair[len(prefix) :]
    return ""


def cookie_header(session: str = "", csrf: str = "") -> str:
    """拼浏览器会送回的 Cookie 头。"""
    parts = []
    if session:
        parts.append(f"{SESSION_COOKIE}={session}")
    if csrf:
        parts.append(f"{CSRF_COOKIE}={csrf}")
    return "; ".join(parts)


def _apply(values: Mapping[str, str | None]) -> None:
    for key, value in values.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def db_path(folder: Path) -> Path:
    """每个测试一个 sqlite 文件。"""
    return folder / "auth.sqlite"
