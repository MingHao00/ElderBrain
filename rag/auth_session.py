"""从 Cookie 或 API 密钥认出当前用户。两条路互不读取对方的凭证。"""

from __future__ import annotations

import time
from dataclasses import dataclass

from auth_app import AuthApp
from auth_constants import ROLE_USER, SESSION_COOKIE
from auth_credentials import user_for_api_key
from auth_denylist import jti_revoked
from auth_http import Incoming, bearer_token, read_cookie
from auth_identity import assigned_role
from auth_db import Account
from session_token import issue_session, load_signing_key, verify_session


@dataclass(frozen=True)
class Visitor:
    """提问需要的身份。role 来自令牌，密钥调用不带角色。"""

    user_id: str
    created_at: int
    role: str
    jti: str
    exp: int


def now_seconds() -> int:
    """当前秒。测试可以换掉，避免真的等配额窗口。"""
    return int(time.time())


def issue_for(account: Account, now: int) -> tuple[str, str] | None:
    """按现在的管理员名单签发。没有签名密钥就返回 None。"""
    key = load_signing_key()
    if key is None:
        return None
    role = assigned_role(account.provider, account.email, account.github_id)
    try:
        token = issue_session(key, account.user_id, role, now)
    except ValueError:
        return None
    return token, role


def session_visitor(app: AuthApp, request: Incoming, now: int) -> Visitor | None:
    """网页身份只看会话 Cookie，不看 Authorization。"""
    raw = read_cookie(request.header("cookie"), SESSION_COOKIE)
    key = load_signing_key()
    if not raw or key is None:
        return None
    claims = verify_session(key, raw, now)
    if claims is None or jti_revoked(app.db, claims.jti, now):
        return None
    account = app.db.account_by_id(claims.user_id)
    if account is None:
        return None
    return Visitor(account.user_id, account.created_at, claims.role, claims.jti, claims.exp)


def bearer_visitor(app: AuthApp, request: Incoming) -> Visitor | None:
    """查询接口只看 Bearer 密钥，不看 Cookie。"""
    secret = bearer_token(request.header("authorization"))
    if secret is None:
        return None
    user_id = user_for_api_key(app.db, secret)
    if user_id is None:
        return None
    account = app.db.account_by_id(user_id)
    if account is None:
        return None
    return Visitor(account.user_id, account.created_at, ROLE_USER, "", 0)
