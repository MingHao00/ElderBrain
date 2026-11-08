"""提问接口写入审计。不提供查询接口。"""

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from audit_log import AUDIT_WRITE_FAILED, UNKNOWN_USER_ID
from audit_record import CITATION_UNCHECKED, LATENCY_STAGES
from auth_app import AuthApp
from auth_constants import (
    HTTP_OK,
    HTTP_TOO_MANY,
    HTTP_UNAUTHORIZED,
    HTTP_UNPROCESSABLE,
    PATH_ASK,
    PATH_KEYS,
    PATH_QUERY,
    QUESTION_TOO_LONG,
)
from auth_db import Account, AuthDb
from auth_identity import hash_ip, ip_salt
from auth_quota import reserve_quota, yuan_to_micro
from auth_routes import route_api
from auth_session import issue_for, now_seconds
from auth_testkit import (
    TEST_IP,
    auth_env,
    cookie_header,
    json_bytes,
    make_request,
    read_body,
    read_events,
)
from demo_answer import MAX_BODY_BYTES, MAX_QUESTION_CHARS
from demo_server import DemoHandler

# 形状像令牌，但不是任何服务的密钥。
_FAKE_TOKEN = "sk-placeholder-not-a-real-key"
_HIDDEN_QUESTION = "拒绝时不记的问题"
_TABLE = "request_audits"


class QuestionAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = auth_env()
        self._env.__enter__()
        self._dir = tempfile.TemporaryDirectory()
        self.calls: list[str] = []
        self.app = AuthApp(
            AuthDb(Path(self._dir.name) / "auth.sqlite"),
            lambda _recipient, _code: None,
            None,
        )

    def tearDown(self) -> None:
        self._dir.cleanup()
        self._env.__exit__(None, None, None)

    def test_success_records_unchecked_citation(self) -> None:
        email = "ask@example.com"
        question = "火球术射程"
        account, headers, _token = self._session(email)
        asked = self._post(PATH_ASK, {"question": question}, headers)
        self.assertEqual(asked.status, HTTP_OK)
        created = read_body(self._post(PATH_KEYS, {}, headers))
        secret = str(created["key"])
        queried = self._post(
            PATH_QUERY,
            {"question": question},
            {"authorization": f"Bearer {secret}"},
        )
        self.assertEqual(queried.status, HTTP_OK)
        rows = _audits(self.app.db.path)
        self.assertEqual(len(rows), 2)
        stored = Path(self.app.db.path).read_bytes()
        self.assertNotIn(secret.encode("utf-8"), stored)
        for row in rows:
            self.assertEqual(row["http_status"], HTTP_OK)
            record = json.loads(str(row["record_json"]))
            self.assertEqual(record["citation_check"], CITATION_UNCHECKED)
            self.assertEqual(record["question"], question)
            self.assertEqual(record["user_id"], account.user_id)
            self.assertEqual(record["retrieved"], [])
            self.assertEqual(record["cited"], [])
            self.assertEqual(record["tokens_in"], 0)
            self.assertEqual(record["tokens_out"], 0)
            self.assertEqual(record["cost_estimate"], 0)
            self.assertEqual(record["latency_ms"], {stage: 0 for stage in LATENCY_STAGES})
            self.assertNotIn(email, json.dumps(record, ensure_ascii=False))

    def test_denied_records_omit_token(self) -> None:
        denied = self._post(
            PATH_ASK,
            {"question": _HIDDEN_QUESTION},
            {"authorization": f"Bearer {_FAKE_TOKEN}", "cookie": _FAKE_TOKEN},
        )
        self.assertEqual(denied.status, HTTP_UNAUTHORIZED)
        account, headers, token = self._session("quota@example.com")
        reserve_quota(
            self.app.db,
            account.user_id,
            account.created_at,
            yuan_to_micro(21),
            now_seconds(),
        )
        blocked_headers = dict(headers)
        blocked_headers["authorization"] = f"Bearer {_FAKE_TOKEN}"
        blocked = self._post(PATH_ASK, {"question": _HIDDEN_QUESTION}, blocked_headers)
        self.assertEqual(blocked.status, HTTP_TOO_MANY)
        self.assertEqual(self.calls, [])
        rows = _audits(self.app.db.path)
        self.assertEqual(
            sorted(row["http_status"] for row in rows),
            [HTTP_UNAUTHORIZED, HTTP_TOO_MANY],
        )
        stored = Path(self.app.db.path).read_bytes()
        self.assertNotIn(_FAKE_TOKEN.encode("ascii"), stored)
        self.assertNotIn(token.split(".")[2].encode("ascii"), stored)
        self.assertNotIn(_HIDDEN_QUESTION.encode("utf-8"), stored)
        self.assertNotIn(TEST_IP.encode("ascii"), stored)
        unauthorized = next(row for row in rows if row["http_status"] == 401)
        limited = next(row for row in rows if row["http_status"] == 429)
        self.assertEqual(unauthorized["user_id"], UNKNOWN_USER_ID)
        self.assertEqual(limited["user_id"], account.user_id)
        self.assertIsNone(unauthorized["record_json"])
        self.assertIsNone(limited["record_json"])

    def test_unprocessable_omits_question_text(self) -> None:
        marker = "审计不应保存这段问题原文"
        question = marker + ("问" * (MAX_QUESTION_CHARS + 1 - len(marker)))
        response = self._post(PATH_ASK, {"question": question}, {})
        self.assertEqual(response.status, HTTP_UNPROCESSABLE)
        self.assertEqual(self.calls, [])
        self._assert_too_long(len(question), marker)

    def test_handler_oversize_omits_unread_body(self) -> None:
        marker = "提前返回不应保存的问题原文"
        length = MAX_BODY_BYTES + 1
        body = _UnreadBody(marker.encode("utf-8"))
        handler = _handler(self.app, PATH_ASK, str(length), body)
        handler.do_POST()
        self.assertFalse(body.read_called)
        status_line = handler.wfile.getvalue().split(b"\r\n", 1)[0]
        self.assertIn(str(HTTP_UNPROCESSABLE).encode("ascii"), status_line)
        self._assert_too_long(length, marker)

    def test_write_failure_keeps_ok_answer(self) -> None:
        _account, headers, _token = self._session("keep@example.com")
        question = "火球术的射程是多少"
        marker = f"{question} {_FAKE_TOKEN}"
        buffer = io.StringIO()
        with patch("auth_gate.record_answer", side_effect=RuntimeError(marker)):
            with redirect_stderr(buffer):
                response = self._post(PATH_ASK, {"question": question}, headers)
        self.assertEqual(response.status, HTTP_OK)
        self.assertEqual(read_events(response)[0]["event"], "version")
        self.assertEqual(read_events(response)[1]["text"], "规则回答")
        logged = buffer.getvalue()
        self.assertEqual(logged.splitlines(), [f"{AUDIT_WRITE_FAILED} RuntimeError"])
        self.assertNotIn(question, logged)
        self.assertNotIn(_FAKE_TOKEN, logged)
        self.assertEqual(_audits(self.app.db.path), [])

    def _assert_too_long(self, length: int, marker: str) -> None:
        rows = _audits(self.app.db.path)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["http_status"], HTTP_UNPROCESSABLE)
        self.assertEqual(row["user_id"], UNKNOWN_USER_ID)
        self.assertEqual(row["reason"], QUESTION_TOO_LONG)
        self.assertEqual(row["question_len"], length)
        self.assertIsNone(row["record_json"])
        stored = Path(self.app.db.path).read_bytes()
        self.assertNotIn(marker.encode("utf-8"), stored)

    def _session(self, email: str) -> tuple[Account, dict[str, str], str]:
        moment = now_seconds()
        salt = ip_salt()
        assert salt is not None
        account = self.app.db.login_email(email, hash_ip(TEST_IP, salt), moment)
        assert isinstance(account, Account)
        issued = issue_for(account, moment)
        assert issued is not None
        token, _role = issued
        csrf = "csrf-token-for-tests-0001"
        headers = {"cookie": cookie_header(token, csrf), "x-csrf-token": csrf}
        return account, headers, token

    def _post(
        self,
        path: str,
        payload: dict[str, object],
        headers: dict[str, str],
    ) -> object:
        request = make_request("POST", path, json_bytes(payload), headers)
        return route_api(self.app, request, self._answer, None)

    def _answer(self, question: str, _history: list[dict[str, str]]) -> str:
        self.calls.append(question)
        return "规则回答"


def _handler(app: AuthApp, path: str, length: str, body: "_UnreadBody") -> DemoHandler:
    handler = DemoHandler.__new__(DemoHandler)
    handler.auth = app
    handler.path = path
    handler.command = "POST"
    handler.headers = _LengthHeader(length)
    handler.rfile = body
    handler.wfile = io.BytesIO()
    handler.request_version = "HTTP/1.1"
    return handler


class _LengthHeader:
    def __init__(self, length: str) -> None:
        self._length = length

    def get(self, name: str, default: str | None = None) -> str | None:
        if name == "Content-Length":
            return self._length
        return default


class _UnreadBody:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.read_called = False

    def read(self, _size: int = -1) -> bytes:
        self.read_called = True
        return self.payload


def _audits(path: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        found = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (_TABLE,),
        ).fetchone()
        if found is None:
            return []
        rows = conn.execute(
            """
            SELECT request_id, user_id, created_at, http_status, reason,
                   question_len, record_json
            FROM request_audits
            """
        ).fetchall()
        return list(rows)
    finally:
        conn.close()


if __name__ == "__main__":
    unittest.main()
