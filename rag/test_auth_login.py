"""邮箱验证码、注册频率和 GitHub 数字编号。"""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from auth_app import AuthApp
from auth_constants import (
    CODE_MAX_ATTEMPTS,
    CODE_TTL_SECONDS,
    CSRF_COOKIE,
    EMAIL_HOURLY_CAP,
    EMAIL_RESEND_SECONDS,
    ERR_EMAIL_UNCONFIGURED,
    ERR_GITHUB_UNCONFIGURED,
    ERR_MAIL_UNCONFIGURED,
    IP_HOURLY_SIGNUPS,
    PATH_EMAIL_CODE,
    PATH_EMAIL_VERIFY,
    PATH_GITHUB_CALLBACK,
    PATH_GITHUB_START,
    PATH_SESSION,
    ROLE_ADMIN,
    SESSION_COOKIE,
)
from auth_credentials import begin_email_code, hash_code, new_login_code, take_email_code
from auth_db import Account, AuthDb
from auth_identity import hash_email, hash_ip
from auth_mail import MailConfigError, send_code_mail
from auth_routes import route_api
from auth_session import now_seconds
from auth_testkit import (
    TEST_IP,
    auth_env,
    cookie_header,
    json_bytes,
    make_request,
    read_body,
    set_cookie,
)
from session_token import load_signing_key, verify_session

_EMAIL = "person@example.com"
_LOGIN = "unique-login-xyz"


class LoginTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = auth_env()
        self._env.__enter__()
        self._dir = tempfile.TemporaryDirectory()
        self.sent: list[tuple[str, str]] = []
        self.app = AuthApp(
            AuthDb(Path(self._dir.name) / "auth.sqlite"),
            self._mailer,
            None,
        )

    def tearDown(self) -> None:
        self._dir.cleanup()
        self._env.__exit__(None, None, None)

    def test_email_login_stores_code_hash_only(self) -> None:
        headers = self._csrf()
        sent = self._post(PATH_EMAIL_CODE, {"email": _EMAIL}, headers)
        self.assertEqual(sent.status, 200)
        self.assertEqual(self.sent[0][0], _EMAIL)
        code = self.sent[0][1]
        self._assert_code_hashed(code)
        verified = self._post(PATH_EMAIL_VERIFY, {"email": _EMAIL, "code": code}, headers)
        body = read_body(verified)
        self.assertEqual(set(body), {"logged_in", "role"})
        self.assertNotIn(_EMAIL, verified.body.decode("utf-8"))
        self.assertEqual(body["logged_in"], True)

    def test_session_cookie_is_host_only(self) -> None:
        headers = self._csrf()
        self._post(PATH_EMAIL_CODE, {"email": "cookie@example.com"}, headers)
        code = self.sent[0][1]
        verified = self._post(
            PATH_EMAIL_VERIFY,
            {"email": "cookie@example.com", "code": code},
            headers,
        )
        self._assert_session_cookie(verified)

    def test_missing_salt_blocks_email_but_not_configured_check(self) -> None:
        os.environ["LOGIN_IP_SALT"] = ""
        headers = self._csrf()
        blocked = self._post(PATH_EMAIL_CODE, {"email": _EMAIL}, headers)
        self.assertEqual(read_body(blocked)["error"], ERR_EMAIL_UNCONFIGURED)
        self.assertEqual(self.sent, [])
        os.environ.pop("GITHUB_CLIENT_ID", None)
        started = route_api(
            self.app,
            make_request("GET", PATH_GITHUB_START),
            _unused,
            None,
        )
        self.assertIn(ERR_GITHUB_UNCONFIGURED, started.body.decode("utf-8"))

    def test_wrong_code_expires_after_five_misses(self) -> None:
        code, digest, salt = new_login_code()
        email_hash = hash_email(_EMAIL)
        now = now_seconds()
        self.assertIsNone(begin_email_code(self.app.db, email_hash, digest, salt, now))
        wrong = "00000000" if code != "00000000" else "11111111"
        for _index in range(CODE_MAX_ATTEMPTS):
            self.assertEqual(take_email_code(self.app.db, email_hash, wrong, now), "bad")
        self.assertEqual(take_email_code(self.app.db, email_hash, code, now), "bad")
        again = begin_email_code(
            self.app.db,
            email_hash,
            digest,
            salt,
            now + 1,
        )
        self.assertEqual(again, "soon")
        self.assertIsNone(
            begin_email_code(
                self.app.db,
                email_hash,
                digest,
                salt,
                now + EMAIL_RESEND_SECONDS,
            )
        )

    def test_session_reports_login_state_only(self) -> None:
        opened = route_api(self.app, make_request("GET", PATH_SESSION), _unused, None)
        body = read_body(opened)
        self.assertEqual(set(body), {"logged_in", "role"})
        self.assertEqual(body["logged_in"], False)
        self.assertIsNone(body["role"])

    def test_code_expires_and_hourly_sends_stop(self) -> None:
        code, digest, salt = new_login_code()
        now = now_seconds()
        expired_hash = hash_email("old@example.com")
        self.assertIsNone(begin_email_code(self.app.db, expired_hash, digest, salt, now))
        expired = take_email_code(
            self.app.db,
            expired_hash,
            code,
            now + CODE_TTL_SECONDS,
        )
        self.assertEqual(expired, "bad")
        hourly_hash = hash_email("cap@example.com")
        for index in range(EMAIL_HOURLY_CAP):
            moment = now + index * EMAIL_RESEND_SECONDS
            saved = begin_email_code(self.app.db, hourly_hash, digest, salt, moment)
            self.assertIsNone(saved)
        blocked = begin_email_code(
            self.app.db,
            hourly_hash,
            digest,
            salt,
            now + EMAIL_HOURLY_CAP * EMAIL_RESEND_SECONDS,
        )
        self.assertEqual(blocked, "hourly")

    def test_signup_cap_is_per_ip_hash(self) -> None:
        now = now_seconds()
        ip_hash = hash_ip(TEST_IP, "test-salt-value")
        first = self.app.db.login_email("a0@example.com", ip_hash, now)
        assert isinstance(first, Account)
        again = self.app.db.login_email("a0@example.com", ip_hash, now)
        assert isinstance(again, Account)
        self.assertEqual(again.user_id, first.user_id)
        for index in range(1, IP_HOURLY_SIGNUPS):
            created = self.app.db.login_email(f"a{index}@example.com", ip_hash, now)
            self.assertIsInstance(created, Account)
        blocked = self.app.db.login_email("full@example.com", ip_hash, now)
        self.assertEqual(blocked, "ip")

    def test_github_callback_trusts_numeric_id(self) -> None:
        os.environ["ADMIN_IDENTITIES"] = "github:42"
        os.environ["GITHUB_CLIENT_ID"] = "client"
        os.environ["GITHUB_CLIENT_SECRET"] = "secret"
        os.environ["GITHUB_REDIRECT_URI"] = "http://127.0.0.1/callback"
        fake = _FakeGithub()
        self.app.github = fake
        started = route_api(self.app, make_request("GET", PATH_GITHUB_START), _unused, None)
        self.assertEqual(started.status, 302)
        location = dict(started.headers)["Location"]
        self.assertNotIn("secret", location)
        state = parse_qs(urlparse(location).query)["state"][0]
        done = route_api(
            self.app,
            make_request(
                "GET",
                PATH_GITHUB_CALLBACK,
                query={"code": "abc", "state": state},
                headers={"cookie": _lax_state_pair(started)},
            ),
            _unused,
            None,
        )
        self.assertEqual(done.status, 302)
        text = done.body.decode("utf-8") + " ".join(value for _key, value in done.headers)
        self.assertNotIn(_LOGIN, text)
        self.assertNotIn("gho_test_token", text)
        self.assertEqual(_github_login(self.app.db.path), _LOGIN)
        self.assertEqual(_github_id(self.app.db.path), "42")
        token = set_cookie(done, "__Host-eb_session")
        key = load_signing_key()
        assert key is not None
        claims = verify_session(key, token, now_seconds())
        assert claims is not None
        self.assertEqual(claims.role, ROLE_ADMIN)
        self.assertNotIn(_LOGIN, token)

    def test_smtp_error_hides_password_and_code(self) -> None:
        saved = os.environ.get("LOGIN_SMTP_PASSWORD")
        os.environ["LOGIN_SMTP_PASSWORD"] = "super-secret-pass"
        os.environ["LOGIN_SMTP_HOST"] = ""
        try:
            with self.assertRaises(MailConfigError) as caught:
                send_code_mail(_EMAIL, "12345678")
        finally:
            if saved is None:
                os.environ.pop("LOGIN_SMTP_PASSWORD", None)
            else:
                os.environ["LOGIN_SMTP_PASSWORD"] = saved
        self.assertEqual(str(caught.exception), ERR_MAIL_UNCONFIGURED)
        self.assertNotIn("super-secret-pass", str(caught.exception))
        self.assertNotIn("12345678", str(caught.exception))

    def _mailer(self, recipient: str, code: str) -> None:
        self.sent.append((recipient, code))

    def _csrf(self) -> dict[str, str]:
        opened = route_api(self.app, make_request("GET", PATH_SESSION), _unused, None)
        csrf = set_cookie(opened, CSRF_COOKIE)
        return {"cookie": cookie_header(csrf=csrf), "x-csrf-token": csrf}

    def _post(self, path: str, payload: dict[str, object], headers: dict[str, str]) -> object:
        request = make_request("POST", path, json_bytes(payload), headers)
        return route_api(self.app, request, _unused, None)

    def _assert_session_cookie(self, outgoing: object) -> None:
        issued = _set_cookie_header(outgoing, SESSION_COOKIE)
        self.assertIn("Secure", issued)
        self.assertIn("HttpOnly", issued)
        self.assertIn("SameSite=Strict", issued)
        self.assertIn("Path=/", issued)
        self.assertNotIn("Domain", issued)
        self.assertNotIn("Max-Age", issued)

    def _assert_code_hashed(self, code: str) -> None:
        conn = sqlite3.connect(self.app.db.path)
        try:
            row = conn.execute("SELECT code_hash, salt FROM email_codes").fetchone()
        finally:
            conn.close()
        self.assertNotEqual(row[0], code)
        self.assertEqual(row[0], hash_code(row[1], code))


class _FakeGithub:
    def post_json(self, url: str, form: dict[str, str]) -> dict[str, object]:
        del url
        self.form = form
        return {"access_token": "gho_test_token"}

    def get_user(self, url: str, access_token: str) -> dict[str, object]:
        del url, access_token
        return {"id": 42, "login": _LOGIN}


def _lax_state_pair(outgoing: object) -> str:
    for key, value in outgoing.headers:
        if key == "Set-Cookie" and "SameSite=Lax" in value:
            return value.split(";", 1)[0]
    raise AssertionError("没有绑定浏览器的 state Cookie")


def _set_cookie_header(outgoing: object, name: str) -> str:
    prefix = name + "="
    for key, value in outgoing.headers:
        if key == "Set-Cookie" and value.startswith(prefix):
            return value
    return ""


def _unused(_question: str, _history: list[dict[str, str]]) -> str:
    return ""


def _github_login(path: Path) -> str:
    return _account_value(path, "SELECT github_login FROM accounts")


def _github_id(path: Path) -> str:
    return _account_value(path, "SELECT github_id FROM accounts")


def _account_value(path: Path, sql: str) -> str:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(sql).fetchone()
    finally:
        conn.close()
    return str(row[0])


if __name__ == "__main__":
    unittest.main()
