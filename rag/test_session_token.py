"""会话令牌和管理员名单。"""

import json
import os
import unittest

from auth_constants import (
    CLOCK_SKEW_SECONDS,
    ROLE_ADMIN,
    ROLE_USER,
    SESSION_TTL_SECONDS,
    TOKEN_ALG,
    TOKEN_AUD,
    TOKEN_ISS,
    TOKEN_TYP,
)
from auth_identity import assigned_role
from auth_testkit import auth_env
from session_token import _encode, _mac, issue_session, load_signing_key, verify_session

NOW = 1_700_000_000


class SessionTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = auth_env()
        self._env.__enter__()
        self.key = load_signing_key()
        assert self.key is not None

    def tearDown(self) -> None:
        self._env.__exit__(None, None, None)

    def test_issues_and_verifies(self) -> None:
        token = issue_session(self.key, "u_person", ROLE_USER, NOW)
        claims = verify_session(self.key, token, NOW)
        self.assertIsNotNone(claims)
        assert claims is not None
        self.assertEqual(claims.user_id, "u_person")
        self.assertEqual(claims.role, ROLE_USER)
        self.assertEqual(claims.exp - claims.iat, SESSION_TTL_SECONDS)
        self.assertNotIn("@", token)
        self.assertIsNone(verify_session(self.key, token[:-2] + "aa", NOW))

    def test_rejects_alg_none_and_jku(self) -> None:
        payload = _payload("u_person", ROLE_USER)
        bare = _segments({"alg": "none", "typ": TOKEN_TYP}, payload, self.key)
        self.assertIsNone(verify_session(self.key, bare, NOW))
        empty = bare.rsplit(".", 1)[0] + "."
        self.assertIsNone(verify_session(self.key, empty, NOW))
        headed = {"alg": TOKEN_ALG, "typ": TOKEN_TYP, "jku": "https://evil.example/jwk"}
        signed = _segments(headed, payload, self.key)
        self.assertIsNone(verify_session(self.key, signed, NOW))

    def test_rejects_expired_and_overlong_tokens(self) -> None:
        token = issue_session(self.key, "u_person", ROLE_USER, NOW)
        still = NOW + SESSION_TTL_SECONDS + CLOCK_SKEW_SECONDS
        late = still + 1
        self.assertIsNotNone(verify_session(self.key, token, still))
        self.assertIsNone(verify_session(self.key, token, late))
        payload = _payload("u_person", ROLE_USER)
        payload["exp"] = payload["iat"] + SESSION_TTL_SECONDS + 1
        longer = _segments({"alg": TOKEN_ALG, "typ": TOKEN_TYP}, payload, self.key)
        self.assertIsNone(verify_session(self.key, longer, NOW))

    def test_admin_list_is_fixed_when_issued(self) -> None:
        os.environ["ADMIN_IDENTITIES"] = "github:042, email:Owner@Example.com"
        github_admin = assigned_role("github", None, "42")
        github_user = assigned_role("github", None, "7")
        mail_admin = assigned_role("email", "owner@example.com", None)
        self.assertEqual(github_admin, ROLE_ADMIN)
        self.assertEqual(github_user, ROLE_USER)
        self.assertEqual(mail_admin, ROLE_ADMIN)
        self.assertEqual(assigned_role("github", None, "octocat"), ROLE_USER)
        token = issue_session(self.key, "u_admin", github_admin, NOW)
        os.environ["ADMIN_IDENTITIES"] = ""
        claims = verify_session(self.key, token, NOW)
        assert claims is not None
        self.assertEqual(claims.role, ROLE_ADMIN)
        self.assertEqual(assigned_role("github", None, "42"), ROLE_USER)
        self.assertNotIn("owner@example.com", token)

    def test_rejects_personal_claims_and_missing_key(self) -> None:
        payload = _payload("u_person", ROLE_USER)
        payload["email"] = "hidden@example.com"
        leaked = _segments({"alg": TOKEN_ALG, "typ": TOKEN_TYP}, payload, self.key)
        self.assertIsNone(verify_session(self.key, leaked, NOW))
        os.environ["JWT_SIGNING_KEY"] = ""
        self.assertIsNone(load_signing_key())
        os.environ["JWT_SIGNING_KEY"] = "YQ=="
        self.assertIsNone(load_signing_key())


def _payload(user_id: str, role: str) -> dict[str, object]:
    return {
        "iss": TOKEN_ISS,
        "aud": TOKEN_AUD,
        "sub": user_id,
        "role": role,
        "iat": NOW,
        "nbf": NOW,
        "exp": NOW + SESSION_TTL_SECONDS,
        "jti": "ab" * 16,
    }


def _segments(header: dict[str, str], payload: dict[str, object], key: bytes) -> str:
    head = _encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    body = _encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    mac = _encode(_mac(key, f"{head}.{body}"))
    return f"{head}.{body}.{mac}"


if __name__ == "__main__":
    unittest.main()
