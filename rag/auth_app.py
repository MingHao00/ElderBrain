"""登录服务的组合根。数据库、发信和 GitHub 调用从这里接上。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from auth_db import AuthDb, default_db_path
from auth_github import GithubTransport
from auth_mail import send_code_mail


@dataclass
class AuthApp:
    """一次进程里的登录依赖。测试可以换掉发信、GitHub 和费用告警。"""

    db: AuthDb
    mailer: Callable[[str, str], None]
    github: GithubTransport | None = None
    # 缺省走告警邮局。测试注入后不连接 SMTP。
    alert_deliver: Callable[[str, str], None] | None = None


def open_auth(path: Path | None = None) -> AuthApp:
    """打开本机 sqlite，并用环境变量里的邮局和 GitHub。"""
    return AuthApp(AuthDb(path or default_db_path()), send_code_mail, None)
