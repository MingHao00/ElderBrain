"""Cookie、CSRF 和 JSON 响应。会话令牌本身不放进脚本能读的 Cookie。"""

from __future__ import annotations

import hmac
import json
import secrets
from dataclasses import dataclass
from typing import Mapping

from auth_constants import (
    CSRF_COOKIE,
    CSRF_HEADER,
    CSRF_TOKEN_BYTES,
    JSON_TYPE,
    SESSION_COOKIE,
    TEXT_TYPE,
)


@dataclass(frozen=True)
class Incoming:
    """一次 HTTP 请求里登录层需要的字段。"""

    method: str
    path: str
    query: Mapping[str, str]
    headers: Mapping[str, str]
    body: bytes
    client_ip: str

    def header(self, name: str) -> str | None:
        """按小写名字读头。没有就返回 None。"""
        return self.headers.get(name.lower())


@dataclass(frozen=True)
class Outgoing:
    """一次 HTTP 响应。Set-Cookie 放在 headers 里。"""

    status: int
    body: bytes
    content_type: str
    headers: tuple[tuple[str, str], ...] = ()


def json_response(
    status: int,
    payload: dict[str, object],
    cookies: tuple[str, ...] = (),
) -> Outgoing:
    """JSON 响应。ensure_ascii 关掉，中文错误才能原样返回。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return Outgoing(status, body, JSON_TYPE, _cookies(cookies))


def json_error(status: int, message: str, cookies: tuple[str, ...] = ()) -> Outgoing:
    """错误只放一句文案，不附带邮箱或用户名。"""
    return json_response(status, {"error": message}, cookies)


def text_response(status: int, message: str, cookies: tuple[str, ...] = ()) -> Outgoing:
    """给浏览器直接打开的登录跳转用。"""
    return Outgoing(status, message.encode("utf-8"), TEXT_TYPE, _cookies(cookies))


def redirect_response(location: str, cookies: tuple[str, ...] = ()) -> Outgoing:
    """登录完成后只回到本站首页，不跟用户提供的地址。"""
    headers = (("Location", location),) + _cookies(cookies)
    return Outgoing(302, b"", TEXT_TYPE, headers)


def session_cookie(token: str) -> str:
    """会话 Cookie。不设 Domain，也不设 Max-Age。"""
    return f"{SESSION_COOKIE}={token}; Path=/; Secure; HttpOnly; SameSite=Strict"


def clear_session_cookie() -> str:
    """Max-Age=0 用来删除 Cookie，不是给会话令牌设持久期。"""
    return f"{SESSION_COOKIE}=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0"


def csrf_cookie(token: str) -> str:
    """非 HttpOnly，页面才能抄进请求头。SameSite 只是附加约束。"""
    return f"{CSRF_COOKIE}={token}; Path=/; Secure; SameSite=Strict"


def ensure_csrf(cookie_header: str | None) -> str | None:
    """没有 CSRF Cookie 时返回一条 Set-Cookie。已有就不动。"""
    if read_cookie(cookie_header, CSRF_COOKIE):
        return None
    return csrf_cookie(secrets.token_urlsafe(CSRF_TOKEN_BYTES))


def csrf_ok(request: Incoming) -> bool:
    """请求头必须和 CSRF Cookie 一致。缺一个就是 403。"""
    cookie = read_cookie(request.header("cookie"), CSRF_COOKIE)
    header = request.header(CSRF_HEADER)
    if not cookie or not header or len(cookie) != len(header):
        return False
    return _same(cookie, header)


def read_cookie(header: str | None, name: str) -> str | None:
    """从 Cookie 头取出一个值。不读属性。"""
    if not header:
        return None
    prefix = name + "="
    for part in header.split(";"):
        item = part.strip()
        if item.startswith(prefix):
            return item[len(prefix) :] or None
    return None


def bearer_token(header: str | None) -> str | None:
    """只接受 Bearer 方案。查询接口用它，不用 Cookie。"""
    if not header:
        return None
    scheme, sep, token = header.partition(" ")
    if not sep or scheme.lower() != "bearer":
        return None
    cleaned = token.strip()
    if not cleaned or " " in cleaned:
        return None
    return cleaned


def read_json(body: bytes) -> dict[str, object] | None:
    """正文必须是 JSON 对象。"""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _cookies(cookies: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    return tuple(("Set-Cookie", cookie) for cookie in cookies)


def _same(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)
