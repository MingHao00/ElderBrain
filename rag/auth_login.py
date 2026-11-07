"""邮箱、登出和密钥接口。GitHub 跳转在 auth_github_flow.py。"""

from __future__ import annotations

from auth_app import AuthApp
from auth_constants import (
    ERR_CODE,
    ERR_CODE_LATER,
    ERR_EMAIL,
    ERR_EMAIL_UNCONFIGURED,
    ERR_FORBIDDEN,
    ERR_JSON,
    ERR_KEY_LIMIT,
    ERR_KEY_MISSING,
    ERR_LOGIN_UNAVAILABLE,
    ERR_MAIL_FAILED,
    ERR_SIGNUP_LIMIT,
    ERR_UNAUTHORIZED,
    PATH_EMAIL_CODE,
    PATH_EMAIL_VERIFY,
    PATH_GITHUB_CALLBACK,
    PATH_GITHUB_START,
    PATH_KEYS,
    PATH_KEYS_REVOKE,
    PATH_LOGOUT,
    PATH_SESSION,
    SAFE_MAIL_ERRORS,
)
from auth_credentials import (
    begin_email_code,
    cancel_email_code,
    create_api_key,
    new_login_code,
    revoke_api_key,
    take_email_code,
)
from auth_db import Account
from auth_denylist import revoke_jti
from auth_github_flow import github_callback, github_start
from auth_http import (
    Incoming,
    Outgoing,
    clear_session_cookie,
    csrf_ok,
    ensure_csrf,
    json_error,
    json_response,
    read_json,
    session_cookie,
)
from auth_identity import hash_email, hash_ip, ip_salt, normalize_email
from auth_mail import MailConfigError
from auth_session import issue_for, now_seconds, session_visitor
from session_token import load_signing_key


def handle_login(app: AuthApp, request: Incoming) -> Outgoing | None:
    """处理登录相关路径。不是这些路径就交给提问层。"""
    found = _get(app, request)
    if found is not None or request.method != "POST":
        return found
    return _post(app, request)


def show_session(app: AuthApp, request: Incoming) -> Outgoing:
    """只返回是否登录和角色，并在缺 CSRF Cookie 时发一枚。"""
    visitor = session_visitor(app, request, now_seconds())
    if visitor is None:
        payload: dict[str, object] = {"logged_in": False, "role": None}
    else:
        payload = {"logged_in": True, "role": visitor.role}
    cookie = ensure_csrf(request.header("cookie"))
    cookies = () if cookie is None else (cookie,)
    return json_response(200, payload, cookies)


def _get(app: AuthApp, request: Incoming) -> Outgoing | None:
    if request.method != "GET":
        return None
    if request.path == PATH_SESSION:
        return show_session(app, request)
    if request.path == PATH_GITHUB_START:
        return github_start(app)
    if request.path == PATH_GITHUB_CALLBACK:
        return github_callback(app, request)
    return None


def _post(app: AuthApp, request: Incoming) -> Outgoing | None:
    if request.path == PATH_EMAIL_CODE:
        return email_code(app, request)
    if request.path == PATH_EMAIL_VERIFY:
        return email_verify(app, request)
    if request.path == PATH_LOGOUT:
        return logout(app, request)
    if request.path == PATH_KEYS:
        return create_key(app, request)
    if request.path == PATH_KEYS_REVOKE:
        return revoke_key(app, request)
    return None


def email_code(app: AuthApp, request: Incoming) -> Outgoing:
    """发送验证码。没有 IP 盐或签名密钥时拒绝，不跳过限制。"""
    if not csrf_ok(request):
        return json_error(403, ERR_FORBIDDEN)
    closed = _email_closed()
    if closed is not None:
        return closed
    email = _email_from(request)
    if isinstance(email, Outgoing):
        return email
    return _send_code(app, email)


def email_verify(app: AuthApp, request: Incoming) -> Outgoing:
    """对上验证码后签发会话。错误文案不带回邮箱或验证码。"""
    if not csrf_ok(request):
        return json_error(403, ERR_FORBIDDEN)
    salt = ip_salt()
    if salt is None or load_signing_key() is None:
        return _closed_reason(salt)
    parsed = _code_from(request)
    if isinstance(parsed, Outgoing):
        return parsed
    email, code = parsed
    return _accept_code(app, request, email, code, salt)


def logout(app: AuthApp, request: Incoming) -> Outgoing:
    """把 jti 写入拒绝名单，并删除 Cookie。只删 Cookie 不算登出。"""
    if not csrf_ok(request):
        return json_error(403, ERR_FORBIDDEN)
    now = now_seconds()
    visitor = session_visitor(app, request, now)
    if visitor is None:
        return json_error(401, ERR_UNAUTHORIZED, (clear_session_cookie(),))
    revoke_jti(app.db, visitor.jti, visitor.exp, now)
    return json_response(200, {"logged_in": False}, (clear_session_cookie(),))


def create_key(app: AuthApp, request: Incoming) -> Outgoing:
    """登录会话里创建密钥。完整密钥只在这一次响应里出现。"""
    visitor = session_visitor(app, request, now_seconds())
    if visitor is None:
        return json_error(401, ERR_UNAUTHORIZED)
    if not csrf_ok(request):
        return json_error(403, ERR_FORBIDDEN)
    created = create_api_key(app.db, visitor.user_id, now_seconds())
    if created is None:
        return json_error(409, ERR_KEY_LIMIT)
    secret, key_id = created
    return json_response(200, {"key": secret, "key_id": key_id})


def revoke_key(app: AuthApp, request: Incoming) -> Outgoing:
    """吊销自己的密钥。别人的 key_id 和没有的一样拒绝。"""
    visitor = session_visitor(app, request, now_seconds())
    if visitor is None:
        return json_error(401, ERR_UNAUTHORIZED)
    if not csrf_ok(request):
        return json_error(403, ERR_FORBIDDEN)
    key_id = _key_id(request)
    if key_id is None:
        return json_error(400, ERR_KEY_MISSING)
    if not revoke_api_key(app.db, visitor.user_id, key_id, now_seconds()):
        return json_error(400, ERR_KEY_MISSING)
    return json_response(200, {"revoked": True})


def _send_code(app: AuthApp, email: str) -> Outgoing:
    code, code_hash, salt = new_login_code()
    email_hash = hash_email(email)
    now = now_seconds()
    outcome = begin_email_code(app.db, email_hash, code_hash, salt, now)
    if outcome is not None:
        return json_error(429, ERR_CODE_LATER)
    try:
        app.mailer(email, code)
    except (MailConfigError, OSError) as exc:
        cancel_email_code(app.db, email_hash, now)
        return json_error(400, _mail_message(exc))
    return json_response(200, {"sent": True})


def _accept_code(
    app: AuthApp,
    request: Incoming,
    email: str,
    code: str,
    salt: str,
) -> Outgoing:
    now = now_seconds()
    verdict = take_email_code(app.db, hash_email(email), code, now)
    if verdict != "ok":
        return json_error(400, ERR_CODE)
    account = app.db.login_email(email, hash_ip(request.client_ip, salt), now)
    if isinstance(account, str):
        return json_error(429, ERR_SIGNUP_LIMIT)
    return _session_body(account, now)


def _session_body(account: Account, now: int) -> Outgoing:
    issued = issue_for(account, now)
    if issued is None:
        return json_error(400, ERR_LOGIN_UNAVAILABLE)
    token, role = issued
    payload = {"logged_in": True, "role": role}
    return json_response(200, payload, (session_cookie(token),))


def _email_from(request: Incoming) -> str | Outgoing:
    payload = read_json(request.body)
    if payload is None:
        return json_error(400, ERR_JSON)
    email = normalize_email(payload.get("email"))
    if email is None:
        return json_error(400, ERR_EMAIL)
    return email


def _code_from(request: Incoming) -> tuple[str, str] | Outgoing:
    payload = read_json(request.body)
    if payload is None:
        return json_error(400, ERR_JSON)
    email = normalize_email(payload.get("email"))
    code = payload.get("code")
    if email is None or not isinstance(code, str) or not code.strip():
        return json_error(400, ERR_CODE)
    return email, code.strip()


def _key_id(request: Incoming) -> str | None:
    payload = read_json(request.body)
    if payload is None:
        return None
    key_id = payload.get("key_id")
    if not isinstance(key_id, str) or not key_id.strip():
        return None
    return key_id.strip()


def _email_closed() -> Outgoing | None:
    if ip_salt() is None:
        return json_error(400, ERR_EMAIL_UNCONFIGURED)
    if load_signing_key() is None:
        return json_error(400, ERR_LOGIN_UNAVAILABLE)
    return None


def _closed_reason(salt: str | None) -> Outgoing:
    if salt is None:
        return json_error(400, ERR_EMAIL_UNCONFIGURED)
    return json_error(400, ERR_LOGIN_UNAVAILABLE)


def _mail_message(exc: Exception) -> str:
    text = str(exc)
    if text in SAFE_MAIL_ERRORS:
        return text
    return ERR_MAIL_FAILED
