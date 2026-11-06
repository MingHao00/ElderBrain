"""告警邮件发送测试。发送动作被替换，不连接 SMTP。"""

import os
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from unittest import mock

import alert_mail
from alert_mail import REQUIRED_ENV_NAMES, send_alert_mail

SMTP_PASSWORD = "unit-test-smtp-secret"
ALERT_SUBJECT = "费用接近上限"
ALERT_BODY = "当日模型费用已达上限的八成"


def _credentials() -> dict[str, str]:
    return {
        "ALERT_SMTP_HOST": "smtp.example.com",
        "ALERT_SMTP_PORT": "587",
        "ALERT_SMTP_USER": "alert@example.com",
        "ALERT_SMTP_PASSWORD": SMTP_PASSWORD,
        "ALERT_MAIL_FROM": "alert@example.com",
        "ALERT_MAIL_TO": "admin@example.com",
    }


@contextmanager
def _env(values: dict[str, str]) -> Iterator[None]:
    """只保留本次给出的告警变量，避免读到本机真实凭据。"""
    with mock.patch.dict(os.environ, values, clear=False):
        for name in REQUIRED_ENV_NAMES:
            if name not in values:
                os.environ.pop(name, None)
        yield


class AlertMailTests(unittest.TestCase):
    def test_missing_env_does_not_send(self) -> None:
        sent: list[str] = []
        values = _credentials()
        values.pop("ALERT_SMTP_HOST")

        def deliver(subject: str, body: str) -> None:
            sent.append(body)

        with _env(values):
            with self.assertRaises(ValueError) as caught:
                send_alert_mail(
                    ALERT_SUBJECT,
                    ALERT_BODY,
                    deliver=deliver,
                )
        self.assertEqual(sent, [])
        self.assertNotIn(SMTP_PASSWORD, str(caught.exception))

    def test_ready_credentials_pass_subject_and_body(self) -> None:
        sent: list[tuple[str, str]] = []

        def deliver(subject: str, body: str) -> None:
            sent.append((subject, body))

        with _env(_credentials()):
            send_alert_mail(ALERT_SUBJECT, ALERT_BODY, deliver=deliver)
        self.assertEqual(sent, [(ALERT_SUBJECT, ALERT_BODY)])
        self.assertNotIn(SMTP_PASSWORD, sent[0][1])

    def test_blank_subject_does_not_send(self) -> None:
        sent: list[str] = []

        def deliver(subject: str, body: str) -> None:
            sent.append(subject)

        with _env(_credentials()):
            with self.assertRaises(ValueError):
                send_alert_mail("", ALERT_BODY, deliver=deliver)
        self.assertEqual(sent, [])

    def test_blank_body_does_not_send(self) -> None:
        sent: list[str] = []

        def deliver(subject: str, body: str) -> None:
            sent.append(subject)

        with _env(_credentials()):
            with self.assertRaises(ValueError):
                send_alert_mail(ALERT_SUBJECT, "  ", deliver=deliver)
        self.assertEqual(sent, [])

    def test_send_failure_redacts_password(self) -> None:
        def deliver(subject: str, body: str) -> None:
            raise OSError(f"login rejected {SMTP_PASSWORD}")

        with _env(_credentials()):
            with self.assertLogs(alert_mail.__name__, level="ERROR") as logs:
                with self.assertRaises(RuntimeError) as caught:
                    send_alert_mail(
                        ALERT_SUBJECT,
                        ALERT_BODY,
                        deliver=deliver,
                    )
        combined = str(caught.exception) + "\n" + "\n".join(logs.output)
        self.assertNotIn(SMTP_PASSWORD, combined)
        self.assertIn("login rejected", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
