"""提问门闩、密钥和登出。"""

import hashlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from auth_app import AuthApp
from auth_constants import (
    CSRF_COOKIE,
    ERR_ANSWER_FAILED,
    ERR_FORBIDDEN,
    ERR_KEY_LIMIT,
    ERR_UNAUTHORIZED,
    NEW_ACCOUNT_WINDOW_SECONDS,
    PATH_ASK,
    PATH_KEYS,
    PATH_KEYS_REVOKE,
    PATH_LOGOUT,
    PATH_QUERY,
    SESSION_COOKIE,
)
from auth_db import Account, AuthDb
from auth_identity import hash_ip, ip_salt
from auth_quota import open_uses, reserve_quota, yuan_to_micro
from auth_routes import route_api
from auth_session import issue_for, now_seconds
from auth_testkit import cookie_header, json_bytes, make_request, read_body, set_cookie
from auth_testkit import auth_env
from demo_answer import MAX_QUESTION_CHARS

_GAP = 61


class AccessTests(unittest.TestCase):
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

    def test_empty_or_long_question_does_not_spend(self) -> None:
        account, headers = self._session("empty@example.com")
        empty = self._ask({"question": "  "}, headers)
        long_text = "问" * (MAX_QUESTION_CHARS + 1)
        huge = self._ask({"question": long_text}, headers)
        self.assertEqual(empty.status, 422)
        self.assertEqual(huge.status, 422)
        self.assertEqual(self.calls, [])
        self.assertEqual(open_uses(self.app.db, account.user_id), 0)

    def test_missing_csrf_is_forbidden(self) -> None:
        account, headers = self._session("csrf@example.com")
        headers.pop("x-csrf-token")
        response = self._ask({"question": "火球术射程"}, headers)
        self.assertEqual(response.status, 403)
        self.assertEqual(read_body(response)["error"], ERR_FORBIDDEN)
        self.assertEqual(self.calls, [])
        self.assertEqual(open_uses(self.app.db, account.user_id), 0)

    def test_logout_rejects_the_jti(self) -> None:
        _account, headers, token = self._session("bye@example.com", with_token=True)
        blocked = self._post(PATH_LOGOUT, {}, _without(headers, "x-csrf-token"))
        self.assertEqual(blocked.status, 403)
        done = self._post(PATH_LOGOUT, {}, headers)
        self.assertEqual(done.status, 200)
        cleared = set_cookie(done, SESSION_COOKIE)
        self.assertEqual(cleared, "")
        self.assertIn("Max-Age=0", done.headers[0][1])
        again = self._ask({"question": "火球术射程"}, headers)
        self.assertEqual(again.status, 401)
        signature = token.split(".")[2]
        stored = Path(self.app.db.path).read_bytes()
        self.assertNotIn(signature.encode("ascii"), stored)

    def test_keys_are_hashed_and_the_third_is_refused(self) -> None:
        _account, headers = self._session("keys@example.com")
        first = read_body(self._post(PATH_KEYS, {}, headers))
        second = read_body(self._post(PATH_KEYS, {}, headers))
        third = self._post(PATH_KEYS, {}, headers)
        self.assertEqual(third.status, 409)
        self.assertEqual(read_body(third)["error"], ERR_KEY_LIMIT)
        secret = str(first["key"])
        self._assert_hash_only(secret)
        revoked = self._post(PATH_KEYS_REVOKE, {"key_id": first["key_id"]}, headers)
        self.assertEqual(revoked.status, 200)
        denied = self._query(secret, headers)
        self.assertEqual(denied.status, 401)
        created = self._post(PATH_KEYS, {}, headers)
        self.assertEqual(created.status, 200)
        fresh = str(read_body(created)["key"])
        allowed = self._query(fresh, headers)
        self.assertEqual(allowed.status, 200)
        self.assertEqual(str(second["key"]) != secret, True)

    def test_query_ignores_cookie_and_ask_ignores_key(self) -> None:
        owner, owner_headers, token = self._session("owner@example.com", with_token=True)
        other, other_headers = self._session("other@example.com")
        created = read_body(self._post(PATH_KEYS, {}, other_headers))
        secret = str(created["key"])
        only_cookie = self._query("", owner_headers)
        self.assertEqual(only_cookie.status, 401)
        self.assertEqual(read_body(only_cookie)["error"], ERR_UNAUTHORIZED)
        both = self._query(secret, owner_headers)
        self.assertEqual(both.status, 200)
        self.assertEqual(open_uses(self.app.db, other.user_id), 1)
        self.assertEqual(open_uses(self.app.db, owner.user_id), 0)
        only_key = make_request(
            "POST",
            PATH_ASK,
            json_bytes({"question": "火球术射程"}),
            {"authorization": f"Bearer {secret}"},
        )
        refused = route_api(self.app, only_key, self._answer, None)
        self.assertEqual(refused.status, 401)
        self.assertEqual(open_uses(self.app.db, other.user_id), 1)
        request = make_request(
            "POST",
            PATH_QUERY,
            json_bytes({"question": "火球术射程"}),
            {"authorization": f"Bearer {token}", "cookie": owner_headers["cookie"]},
        )
        rejected = route_api(self.app, request, self._answer, None)
        self.assertEqual(rejected.status, 401)

    def test_ask_closes_without_signing_key(self) -> None:
        _account, headers = self._session("closed@example.com")
        os.environ["JWT_SIGNING_KEY"] = ""
        response = self._ask({"question": "火球术射程"}, headers)
        self.assertEqual(response.status, 401)
        self.assertEqual(read_body(response)["error"], ERR_UNAUTHORIZED)
        self.assertEqual(self.calls, [])

    def test_ask_without_credentials_is_unauthorized(self) -> None:
        request = make_request(
            "POST",
            PATH_ASK,
            json_bytes({"question": "火球术射程"}),
        )
        response = route_api(self.app, request, self._answer, None)
        self.assertEqual(response.status, 401)
        self.assertEqual(read_body(response)["error"], ERR_UNAUTHORIZED)
        self.assertEqual(self.calls, [])

    def test_model_failure_releases_quota(self) -> None:
        account, headers = self._session("boom@example.com")

        def explode(_question: str, _history: list[dict[str, str]]) -> str:
            raise RuntimeError("model down")

        response = route_api(
            self.app,
            self._question(PATH_ASK, {"question": "火球术射程"}, headers),
            explode,
            None,
        )
        self.assertEqual(response.status, 500)
        self.assertEqual(read_body(response)["error"], ERR_ANSWER_FAILED)
        self.assertNotIn("model down", response.body.decode("utf-8"))
        self.assertEqual(open_uses(self.app.db, account.user_id), 0)

    def test_site_cap_blocks_the_ask_route(self) -> None:
        account, headers = self._session("site@example.com", age=NEW_ACCOUNT_WINDOW_SECONDS + _GAP)
        moment = now_seconds()
        reserve_quota(
            self.app.db,
            account.user_id,
            account.created_at,
            yuan_to_micro(21),
            moment,
        )
        response = self._ask({"question": "火球术射程"}, headers)
        self.assertEqual(response.status, 429)
        self.assertIn("全站上限", str(read_body(response)["error"]))
        self.assertEqual(self.calls, [])

    def _session(
        self,
        email: str,
        with_token: bool = False,
        age: int = 0,
    ) -> tuple[Account, dict[str, str]] | tuple[Account, dict[str, str], str]:
        moment = now_seconds() - age
        salt = ip_salt()
        assert salt is not None
        account = self.app.db.login_email(email, hash_ip("203.0.113.8", salt), moment)
        assert isinstance(account, Account)
        issued = issue_for(account, now_seconds())
        assert issued is not None
        token, _role = issued
        csrf = "csrf-token-for-tests-0001"
        headers = {
            "cookie": cookie_header(token, csrf),
            "x-csrf-token": csrf,
        }
        if with_token:
            return account, headers, token
        return account, headers

    def _ask(self, payload: dict[str, object], headers: dict[str, str]) -> object:
        return self._post(PATH_ASK, payload, headers)

    def _post(self, path: str, payload: dict[str, object], headers: dict[str, str]) -> object:
        return route_api(self.app, self._question(path, payload, headers), self._answer, None)

    def _query(self, secret: str, headers: dict[str, str]) -> object:
        merged = {"cookie": headers["cookie"]}
        if secret:
            merged["authorization"] = f"Bearer {secret}"
        request = self._question(PATH_QUERY, {"question": "火球术射程"}, merged)
        return route_api(self.app, request, self._answer, None)

    def _question(
        self,
        path: str,
        payload: dict[str, object],
        headers: dict[str, str],
    ) -> object:
        return make_request("POST", path, json_bytes(payload), headers)

    def _answer(self, question: str, _history: list[dict[str, str]]) -> str:
        self.calls.append(question)
        return "规则回答"

    def _assert_hash_only(self, secret: str) -> None:
        stored = Path(self.app.db.path).read_bytes()
        self.assertNotIn(secret.encode("utf-8"), stored)
        conn = sqlite3.connect(self.app.db.path)
        try:
            rows = conn.execute("SELECT key_hash FROM api_keys").fetchall()
        finally:
            conn.close()
        digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        hashes = [item[0] for item in rows]
        self.assertIn(digest, hashes)
        self.assertNotIn(secret, hashes)


def _without(headers: dict[str, str], name: str) -> dict[str, str]:
    copied = dict(headers)
    copied.pop(name, None)
    return copied


if __name__ == "__main__":
    unittest.main()
