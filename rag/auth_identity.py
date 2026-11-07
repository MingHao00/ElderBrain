"""身份编号、管理员名单和 IP 哈希。名单只在签发时读一次。"""

from __future__ import annotations

import hashlib
import os
import secrets

from auth_constants import (
    EMAIL_MAX_CHARS,
    PROVIDER_EMAIL,
    PROVIDER_GITHUB,
    ROLE_ADMIN,
    ROLE_USER,
    USER_ID_PREFIX,
    USER_ID_RANDOM_BYTES,
)

_GITHUB_MARK = "github:"
_EMAIL_MARK = "email:"


def new_user_id() -> str:
    """系统生成的假名编号。不是邮箱，也不是 GitHub 用户名。"""
    return USER_ID_PREFIX + secrets.token_hex(USER_ID_RANDOM_BYTES)


def normalize_email(value: object) -> str | None:
    """收成小写邮箱。不像邮箱就拒绝，调用方不要把原文写进错误。"""
    if not isinstance(value, str):
        return None
    email = value.strip().lower()
    if not email or len(email) > EMAIL_MAX_CHARS or any(ch.isspace() for ch in email):
        return None
    local, mark, domain = email.partition("@")
    if mark != "@" or not local or "." not in domain:
        return None
    return email


def hash_email(email: str) -> str:
    """验证码表只存哈希，明文邮箱留在账号表。"""
    return hashlib.sha256(email.encode("utf-8")).hexdigest()


def ip_salt() -> str | None:
    """盐来自环境变量。没有盐就不能做 IP 限制，调用方必须拒绝。"""
    salt = os.getenv("LOGIN_IP_SALT", "").strip()
    return salt or None


def hash_ip(ip: str, salt: str) -> str:
    """库里只留哈希。盐由调用方传入，不能写死。"""
    material = f"{salt}\n{ip}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def assigned_role(provider: str, email: str | None, github_id: str | None) -> str:
    """按当前名单决定这一次会话的角色。之后改名单不追溯已发出的令牌。"""
    listed = admin_identities()
    if provider == PROVIDER_GITHUB and github_id:
        if f"{_GITHUB_MARK}{github_id}" in listed:
            return ROLE_ADMIN
    if provider == PROVIDER_EMAIL and email:
        if f"{_EMAIL_MARK}{email.strip().lower()}" in listed:
            return ROLE_ADMIN
    return ROLE_USER


def admin_identities() -> frozenset[str]:
    """解析 ADMIN_IDENTITIES。空名单表示没有管理员。非法项直接丢掉。"""
    raw = os.getenv("ADMIN_IDENTITIES", "")
    found = {_normalize_admin(part.strip()) for part in raw.split(",")}
    return frozenset(item for item in found if item)


def _normalize_admin(item: str) -> str | None:
    if item.startswith(_GITHUB_MARK):
        return _github_admin(item[len(_GITHUB_MARK) :])
    if item.startswith(_EMAIL_MARK):
        return _email_admin(item[len(_EMAIL_MARK) :])
    return None


def _github_admin(number: str) -> str | None:
    # 只认数字编号。用户名对上也不算管理员。
    if not number.isdigit():
        return None
    value = int(number)
    if value <= 0:
        return None
    return f"{_GITHUB_MARK}{value}"


def _email_admin(email: str) -> str | None:
    cleaned = normalize_email(email)
    if cleaned is None:
        return None
    return f"{_EMAIL_MARK}{cleaned}"
