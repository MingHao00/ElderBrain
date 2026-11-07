"""邮箱验证码的发信。测试注入别的函数，不连真实邮箱。"""

from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from auth_constants import (
    CODE_TTL_SECONDS,
    ERR_MAIL_FAILED,
    ERR_MAIL_UNCONFIGURED,
    MAIL_SUBJECT,
    SMTP_PORT_MAX,
    SMTP_PORT_MIN,
    SMTP_SSL_PORT,
    SMTP_TIMEOUT_SECONDS,
)


class MailConfigError(Exception):
    """发信失败。消息里不能带密码或验证码。"""


@dataclass(frozen=True)
class SmtpConfig:
    """发信所需的五项。缺任何一项都不发送。"""

    host: str
    port: int
    user: str
    password: str
    sender: str


def send_code_mail(recipient: str, code: str) -> None:
    """把验证码寄出。配置不齐或邮局拒绝时抛出固定文案。"""
    config = smtp_config()
    if config is None:
        raise MailConfigError(ERR_MAIL_UNCONFIGURED)
    message = EmailMessage()
    message["From"] = config.sender
    message["To"] = recipient
    message["Subject"] = MAIL_SUBJECT
    message.set_content(_body(code))
    try:
        _deliver(config, message)
    except OSError as exc:
        raise MailConfigError(ERR_MAIL_FAILED) from exc


def smtp_config() -> SmtpConfig | None:
    """读取 LOGIN_SMTP_* 和 LOGIN_MAIL_FROM。密码不写进返回值以外的地方。"""
    host = os.getenv("LOGIN_SMTP_HOST", "").strip()
    port_text = os.getenv("LOGIN_SMTP_PORT", "").strip()
    user = os.getenv("LOGIN_SMTP_USER", "").strip()
    password = os.getenv("LOGIN_SMTP_PASSWORD", "").strip()
    sender = os.getenv("LOGIN_MAIL_FROM", "").strip()
    if not all((host, port_text, user, password, sender)):
        return None
    port = _port(port_text)
    if port is None:
        return None
    return SmtpConfig(host, port, user, password, sender)


def _deliver(config: SmtpConfig, message: EmailMessage) -> None:
    if config.port == SMTP_SSL_PORT:
        client_cm = smtplib.SMTP_SSL(config.host, config.port, timeout=SMTP_TIMEOUT_SECONDS)
    else:
        client_cm = smtplib.SMTP(config.host, config.port, timeout=SMTP_TIMEOUT_SECONDS)
    with client_cm as client:
        if config.port != SMTP_SSL_PORT:
            client.starttls()
        client.login(config.user, config.password)
        client.send_message(message)


def _body(code: str) -> str:
    minutes = CODE_TTL_SECONDS // 60
    return f"验证码 {code} ，{minutes} 分钟内有效。"


def _port(port_text: str) -> int | None:
    if not port_text.isdigit():
        return None
    port = int(port_text)
    if port < SMTP_PORT_MIN or port > SMTP_PORT_MAX:
        return None
    return port
