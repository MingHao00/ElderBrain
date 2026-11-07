"""GitHub 跳转和回调。登录名只进账号表，不进响应。"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from auth_app import AuthApp
from auth_constants import (
    ERR_GITHUB_FAILED,
    ERR_GITHUB_UNCONFIGURED,
    ERR_LOGIN_UNAVAILABLE,
    ERR_SIGNUP_LIMIT,
    OAUTH_STATE_BYTES,
    OAUTH_STATE_TTL_SECONDS,
)
from auth_denylist import consume_state, save_state
from auth_github import (
    GithubConfigError,
    GithubIdentity,
    UrllibGithub,
    authorize_url,
    fetch_identity,
    github_config,
)
from auth_http import (
    Incoming,
    Outgoing,
    read_cookie,
    redirect_response,
    session_cookie,
    text_response,
)
from auth_identity import hash_ip, ip_salt
from auth_session import issue_for, now_seconds
from session_token import load_signing_key

# __Host- 不能带 Domain。Lax 才能在从 GitHub 顶层跳回时带上。
_STATE_COOKIE = "__Host-eb_oauth_state"


def github_start(app: AuthApp) -> Outgoing:
    """跳到 GitHub。state 记在服务端，并绑到发起登录的浏览器。"""
    config = github_config()
    if config is None:
        return text_response(400, ERR_GITHUB_UNCONFIGURED)
    if load_signing_key() is None:
        return text_response(400, ERR_LOGIN_UNAVAILABLE)
    raw, digest = _oauth_state()
    save_state(app.db, digest, now_seconds() + OAUTH_STATE_TTL_SECONDS)
    client_id, _secret, redirect = config
    url = authorize_url(client_id, redirect, raw)
    return redirect_response(url, (_state_cookie(raw),))


def github_callback(app: AuthApp, request: Incoming) -> Outgoing:
    """用一次性 state 和数字 id 完成登录。"""
    if github_config() is None:
        return text_response(400, ERR_GITHUB_UNCONFIGURED)
    identity = _github_identity(app, request)
    if isinstance(identity, Outgoing):
        return identity
    return _finish_github(app, request, identity)


def _finish_github(app: AuthApp, request: Incoming, identity: GithubIdentity) -> Outgoing:
    now = now_seconds()
    salt = ip_salt()
    ip_hash = None if salt is None else hash_ip(request.client_ip, salt)
    account = app.db.login_github(identity.github_id, identity.login, ip_hash, now)
    if isinstance(account, str):
        return text_response(429, ERR_SIGNUP_LIMIT)
    issued = issue_for(account, now)
    if issued is None:
        return text_response(400, ERR_LOGIN_UNAVAILABLE)
    token, _role = issued
    return redirect_response("/", (session_cookie(token),))


def _github_identity(app: AuthApp, request: Incoming) -> GithubIdentity | Outgoing:
    code = request.query.get("code", "")
    state = request.query.get("state", "")
    if not code or not state or not _browser_state(request, state):
        return text_response(400, ERR_GITHUB_FAILED)
    if not _state_ok(app, state):
        return text_response(400, ERR_GITHUB_FAILED)
    transport = app.github if app.github is not None else UrllibGithub()
    try:
        return fetch_identity(code, transport)
    except GithubConfigError:
        return text_response(400, ERR_GITHUB_FAILED)


def _browser_state(request: Incoming, state: str) -> bool:
    saved = read_cookie(request.header("cookie"), _STATE_COOKIE)
    if saved is None or len(saved) != len(state):
        return False
    return hmac.compare_digest(saved, state)


def _state_cookie(state: str) -> str:
    # 会话 Cookie 仍是 Strict。这里用 Lax，否则从 GitHub 跳回时带不上。
    return f"{_STATE_COOKIE}={state}; Path=/; Secure; HttpOnly; SameSite=Lax"


def _oauth_state() -> tuple[str, str]:
    raw = secrets.token_urlsafe(OAUTH_STATE_BYTES)
    digest = hashlib.sha256(raw.encode("ascii")).hexdigest()
    return raw, digest


def _state_ok(app: AuthApp, state: str) -> bool:
    try:
        digest = hashlib.sha256(state.encode("ascii")).hexdigest()
    except UnicodeEncodeError:
        return False
    return consume_state(app.db, digest, now_seconds())
