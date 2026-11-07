"""用标准库签发和校验 HS256 会话令牌。不读令牌头里的密钥地址。"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass

from auth_constants import (
    CLOCK_SKEW_SECONDS,
    FORBIDDEN_HEADERS,
    JTI_BYTES,
    JTI_HEX_CHARS,
    JWT_KEY_BYTES,
    ROLE_ADMIN,
    ROLE_USER,
    SESSION_CLAIMS,
    SESSION_TTL_SECONDS,
    TOKEN_ALG,
    TOKEN_AUD,
    TOKEN_ISS,
    TOKEN_TYP,
    USER_ID_MAX_CHARS,
)

_ROLES = frozenset({ROLE_USER, ROLE_ADMIN})


@dataclass(frozen=True)
class SessionClaims:
    """校验通过后的会话声明。没有邮箱或 GitHub 用户名。"""

    user_id: str
    role: str
    jti: str
    iat: int
    nbf: int
    exp: int


def load_signing_key() -> bytes | None:
    """读取 JWT_SIGNING_KEY。必须是 32 字节的标准 base64，否则登录失败关闭。"""
    raw = os.getenv("JWT_SIGNING_KEY", "").strip()
    if not raw:
        return None
    try:
        key = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error):
        return None
    if len(key) != JWT_KEY_BYTES:
        return None
    return key


def issue_session(key: bytes, user_id: str, role: str, now: int) -> str:
    """签发 8 小时会话。令牌里只有编号和角色。"""
    if role not in _ROLES or not _subject_ok(user_id):
        raise ValueError("会话声明无效")
    header = {"alg": TOKEN_ALG, "typ": TOKEN_TYP}
    payload = {
        "iss": TOKEN_ISS,
        "aud": TOKEN_AUD,
        "sub": user_id,
        "role": role,
        "iat": now,
        "nbf": now,
        "exp": now + SESSION_TTL_SECONDS,
        "jti": secrets.token_hex(JTI_BYTES),
    }
    return _pack(key, header, payload)


def verify_session(key: bytes, token: str, now: int) -> SessionClaims | None:
    """只接受 HS256。alg=none、过期、声明不全都拒绝。"""
    parts = token.split(".")
    if len(parts) != 3 or not _signature_ok(key, parts):
        return None
    header = _json_part(parts[0])
    payload = _json_part(parts[1])
    if not _header_ok(header):
        return None
    return _claims_ok(payload, now)


def _pack(key: bytes, header: dict[str, str], payload: dict[str, object]) -> str:
    head = _encode(_dumps(header))
    body = _encode(_dumps(payload))
    mac = _mac(key, f"{head}.{body}")
    return f"{head}.{body}.{_encode(mac)}"


def _signature_ok(key: bytes, parts: list[str]) -> bool:
    actual = _decode(parts[2])
    if actual is None:
        return False
    try:
        expected = _mac(key, f"{parts[0]}.{parts[1]}")
    except UnicodeEncodeError:
        return False
    if len(actual) != len(expected):
        return False
    return hmac.compare_digest(expected, actual)


def _header_ok(header: object) -> bool:
    if not isinstance(header, dict):
        return False
    if header.get("alg") != TOKEN_ALG or header.get("typ") != TOKEN_TYP:
        return False
    # 这四项只能用来换密钥。看到就拒绝，避免以后误读。
    return FORBIDDEN_HEADERS.isdisjoint(header)


def _claims_ok(payload: object, now: int) -> SessionClaims | None:
    if not isinstance(payload, dict) or set(payload) != SESSION_CLAIMS:
        return None
    stamps = _stamps(payload)
    if stamps is None or not _window_ok(stamps, now):
        return None
    iat, nbf, exp = stamps
    user_id = payload.get("sub")
    role = payload.get("role")
    jti = payload.get("jti")
    if not _subject_ok(user_id) or role not in _ROLES or not _jti_ok(jti):
        return None
    if payload.get("iss") != TOKEN_ISS or payload.get("aud") != TOKEN_AUD:
        return None
    return SessionClaims(str(user_id), str(role), str(jti), iat, nbf, exp)


def _stamps(payload: dict[str, object]) -> tuple[int, int, int] | None:
    iat = _unix(payload.get("iat"))
    nbf = _unix(payload.get("nbf"))
    exp = _unix(payload.get("exp"))
    if iat is None or nbf is None or exp is None:
        return None
    return iat, nbf, exp


def _window_ok(stamps: tuple[int, int, int], now: int) -> bool:
    iat, nbf, exp = stamps
    if exp != iat + SESSION_TTL_SECONDS or nbf < iat or nbf > exp:
        return False
    if iat > now + CLOCK_SKEW_SECONDS or nbf > now + CLOCK_SKEW_SECONDS:
        return False
    return exp + CLOCK_SKEW_SECONDS >= now


def _subject_ok(user_id: object) -> bool:
    if not isinstance(user_id, str) or not user_id or len(user_id) > USER_ID_MAX_CHARS:
        return False
    return "@" not in user_id and not any(ch.isspace() for ch in user_id)


def _jti_ok(jti: object) -> bool:
    if not isinstance(jti, str) or len(jti) < JTI_HEX_CHARS:
        return False
    return all(ch in "0123456789abcdef" for ch in jti)


def _unix(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _mac(key: bytes, signing_input: str) -> bytes:
    return hmac.new(key, signing_input.encode("ascii"), hashlib.sha256).digest()


def _dumps(value: dict[str, object]) -> bytes:
    text = json.dumps(value, separators=(",", ":"), sort_keys=True)
    return text.encode("utf-8")


def _json_part(segment: str) -> object:
    raw = _decode(segment)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode(text: str) -> bytes | None:
    if not text or any(ch.isspace() for ch in text):
        return None
    padded = text + "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, binascii.Error, UnicodeEncodeError):
        return None
