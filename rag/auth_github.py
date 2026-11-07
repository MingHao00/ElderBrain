"""GitHub 登录。回调只取数字 id，登录名只交给账号表。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from auth_constants import (
    ERR_GITHUB_FAILED,
    ERR_GITHUB_UNCONFIGURED,
    GITHUB_AUTHORIZE_URL,
    GITHUB_SCOPE,
    GITHUB_TOKEN_URL,
    GITHUB_USER_AGENT,
    GITHUB_USER_URL,
    HTTP_TIMEOUT_SECONDS,
)


class GithubConfigError(Exception):
    """GitHub 登录失败。消息里不带登录名或访问令牌。"""


class GithubTransport(Protocol):
    """测试替换这两步，避免访问 GitHub。"""

    def post_json(self, url: str, form: dict[str, str]) -> dict[str, object]:
        """用授权码换访问令牌。"""

    def get_user(self, url: str, access_token: str) -> dict[str, object]:
        """读取用户资料。调用方只使用数字 id。"""


@dataclass(frozen=True)
class GithubIdentity:
    """数字 id 用来认人。login 只写入账号表。"""

    github_id: str
    login: str


def github_config() -> tuple[str, str, str] | None:
    """三项都在才启用 GitHub。缺了不影响已经配好的邮箱登录。"""
    client_id = os.getenv("GITHUB_CLIENT_ID", "").strip()
    secret = os.getenv("GITHUB_CLIENT_SECRET", "").strip()
    redirect = os.getenv("GITHUB_REDIRECT_URI", "").strip()
    if not client_id or not secret or not redirect:
        return None
    return client_id, secret, redirect


def authorize_url(client_id: str, redirect: str, state: str) -> str:
    """拼出授权地址。state 由调用方存进服务端。"""
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect,
            "state": state,
            "scope": GITHUB_SCOPE,
        }
    )
    return f"{GITHUB_AUTHORIZE_URL}?{query}"


def fetch_identity(code: str, transport: GithubTransport) -> GithubIdentity:
    """换成数字 id。不把登录名当作身份。"""
    config = github_config()
    if config is None:
        raise GithubConfigError(ERR_GITHUB_UNCONFIGURED)
    client_id, secret, redirect = config
    payload = transport.post_json(
        GITHUB_TOKEN_URL,
        {
            "client_id": client_id,
            "client_secret": secret,
            "code": code,
            "redirect_uri": redirect,
        },
    )
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise GithubConfigError(ERR_GITHUB_FAILED)
    return _identity(transport.get_user(GITHUB_USER_URL, token))


def _identity(user: dict[str, object]) -> GithubIdentity:
    raw_id = user.get("id")
    if isinstance(raw_id, bool) or not isinstance(raw_id, int) or raw_id <= 0:
        raise GithubConfigError(ERR_GITHUB_FAILED)
    login = user.get("login")
    name = login if isinstance(login, str) else ""
    return GithubIdentity(str(raw_id), name)


class UrllibGithub:
    """真实的 GitHub 调用。错误不带响应正文。"""

    def post_json(self, url: str, form: dict[str, str]) -> dict[str, object]:
        """向 GitHub 交换访问令牌。失败时不带响应正文。"""
        data = urllib.parse.urlencode(form).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": GITHUB_USER_AGENT,
            },
            method="POST",
        )
        return _read_json(request)

    def get_user(self, url: str, access_token: str) -> dict[str, object]:
        """读取 GitHub 用户资料。调用方只留下数字 id。"""
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {access_token}",
                "User-Agent": GITHUB_USER_AGENT,
            },
            method="GET",
        )
        return _read_json(request)


def _read_json(request: urllib.request.Request) -> dict[str, object]:
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, urllib.error.URLError):
        raise GithubConfigError(ERR_GITHUB_FAILED)
    if not isinstance(payload, dict):
        raise GithubConfigError(ERR_GITHUB_FAILED)
    return payload
