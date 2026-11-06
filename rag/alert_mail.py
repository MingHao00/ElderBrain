"""第一期告警邮件。只负责发送，不判断费用、超时或生成失败。"""

import logging
import os
import smtplib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from email.message import EmailMessage

LOGGER = logging.getLogger(__name__)

ENV_SMTP_HOST = "ALERT_SMTP_HOST"
ENV_SMTP_PORT = "ALERT_SMTP_PORT"
ENV_SMTP_USER = "ALERT_SMTP_USER"
ENV_SMTP_PASSWORD = "ALERT_SMTP_PASSWORD"
ENV_MAIL_FROM = "ALERT_MAIL_FROM"
ENV_MAIL_TO = "ALERT_MAIL_TO"

REQUIRED_ENV_NAMES = (
    ENV_SMTP_HOST,
    ENV_SMTP_PORT,
    ENV_SMTP_USER,
    ENV_SMTP_PASSWORD,
    ENV_MAIL_FROM,
    ENV_MAIL_TO,
)

SMTP_PORT_MIN = 1
SMTP_PORT_MAX = 65535
# 465 在握手时就启用 TLS，再发 STARTTLS 会被服务器拒绝。
SMTP_IMPLICIT_TLS_PORT = 465
SMTP_TIMEOUT_SECONDS = 30
REDACTED_SECRET = "[REDACTED]"

AlertDeliver = Callable[[str, str], None]


@dataclass(frozen=True)
class AlertMailSettings:
    """一次发送用的 SMTP 凭据。"""

    host: str
    port: int
    username: str
    mail_from: str
    mail_to: str
    # 默认 repr 会带上字段值，密码因此排除在打印之外。
    password: str = field(repr=False)


def send_alert_mail(
    subject: str,
    body: str,
    *,
    deliver: AlertDeliver | None = None,
) -> None:
    """按环境变量中的凭据发送告警邮件。

    Args:
        subject: 邮件主题。空白则拒绝。
        body: 邮件正文。空白则拒绝。
        deliver: 发送动作。缺省走标准库 SMTP；测试注入后不连网。

    Raises:
        ValueError: 主题、正文或凭据无效。文本不含密码值。
        RuntimeError: 发送失败。文本不含密码值。
    """
    _reject_blank(subject, body)
    settings = _load_settings(os.environ)
    action = deliver if deliver is not None else _bind_smtp(settings)
    try:
        action(subject, body)
    except Exception as error:
        safe_text = _redact(str(error), settings.password)
        # 失败记录只留脱敏原因，不能让处理器打出登录密码。
        LOGGER.error("告警邮件发送失败：%s", safe_text)
        raise RuntimeError(safe_text) from None


def _reject_blank(subject: str, body: str) -> None:
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("告警邮件主题不能为空")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("告警邮件正文不能为空")


def _load_settings(source: Mapping[str, str]) -> AlertMailSettings:
    """空白视为未设置。异常只点名变量，不写入变量的值。"""
    values = {
        name: source.get(name, "").strip()
        for name in REQUIRED_ENV_NAMES
    }
    missing = [name for name in REQUIRED_ENV_NAMES if not values[name]]
    if missing:
        names = "、".join(missing)
        raise ValueError(f"告警邮件缺少环境变量：{names}")
    return AlertMailSettings(
        host=values[ENV_SMTP_HOST],
        port=_parse_port(values[ENV_SMTP_PORT]),
        username=values[ENV_SMTP_USER],
        mail_from=values[ENV_MAIL_FROM],
        mail_to=values[ENV_MAIL_TO],
        password=values[ENV_SMTP_PASSWORD],
    )


def _parse_port(raw_port: str) -> int:
    """不把原始端口写进异常，避免误填的密码被原样抛出。"""
    if not raw_port.isdigit():
        raise ValueError("ALERT_SMTP_PORT 必须是正整数")
    port = int(raw_port)
    if port < SMTP_PORT_MIN or port > SMTP_PORT_MAX:
        raise ValueError("ALERT_SMTP_PORT 必须在 1 到 65535 之间")
    return port


def _bind_smtp(settings: AlertMailSettings) -> AlertDeliver:
    """凭据留在闭包里，调用方注入的动作只接触主题和正文。"""

    def send(subject: str, body: str) -> None:
        _send_smtp(settings, subject, body)

    return send


def _send_smtp(
    settings: AlertMailSettings,
    subject: str,
    body: str,
) -> None:
    message = _build_message(settings, subject, body)
    client_type = _smtp_client_type(settings.port)
    with client_type(
        settings.host,
        settings.port,
        timeout=SMTP_TIMEOUT_SECONDS,
    ) as client:
        _login_and_send(client, settings, message)


def _smtp_client_type(port: int) -> type[smtplib.SMTP]:
    if port == SMTP_IMPLICIT_TLS_PORT:
        return smtplib.SMTP_SSL
    return smtplib.SMTP


def _build_message(
    settings: AlertMailSettings,
    subject: str,
    body: str,
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = settings.mail_to
    message["Subject"] = subject
    # 正文只用调用方给出的内容，登录密码不拼进来。
    message.set_content(body)
    return message


def _login_and_send(
    client: smtplib.SMTP,
    settings: AlertMailSettings,
    message: EmailMessage,
) -> None:
    if settings.port != SMTP_IMPLICIT_TLS_PORT:
        # 非 465 端口默认明文握手，先升级 TLS 再提交密码。
        client.starttls()
    client.login(settings.username, settings.password)
    client.send_message(message)


def _redact(text: str, secret: str) -> str:
    if not secret:
        return text
    return text.replace(secret, REDACTED_SECRET)
