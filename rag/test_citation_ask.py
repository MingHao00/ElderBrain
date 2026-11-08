"""生成之后的引用校验，以及审计里的三种结果。不调用真实模型。"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from audit_record import (
    CITATION_FAILED,
    CITATION_PASSED,
    CITATION_PASSED_AFTER_RETRY,
    CITATION_UNCHECKED,
)
from auth_app import AuthApp
from auth_constants import HTTP_OK, PATH_ASK
from auth_db import Account, AuthDb
from auth_identity import hash_ip, ip_salt
from auth_routes import route_api
from auth_session import issue_for, now_seconds
from auth_testkit import (
    TEST_IP,
    auth_env,
    cookie_header,
    event_text,
    json_bytes,
    make_request,
)
from demo_server import DemoHandler
from system import ElderBrainRag
from test_system import (
    BAD_SENTENCE,
    FIREBALL,
    SOURCE_TEXT,
    _EmptySearch,
    _Explode,
    _Keep,
    _Reply,
    _cited,
    _ready,
)

_PASSED_CALLS = 1
_RETRIED_CALLS = 2


class CitationAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = auth_env()
        self._env.__enter__()
        self._dir = tempfile.TemporaryDirectory()
        self.app = AuthApp(
            AuthDb(Path(self._dir.name) / "auth.sqlite"),
            lambda _recipient, _code: None,
            None,
        )
        self.headers = _session_headers(self.app)

    def tearDown(self) -> None:
        self._dir.cleanup()
        self._env.__exit__(None, None, None)

    def test_three_citation_results_are_audited(self) -> None:
        cases = (
            (_Reply(), CITATION_PASSED, _PASSED_CALLS),
            (
                _Reply(["坏掉的正文", _cited(SOURCE_TEXT, FIREBALL)]),
                CITATION_PASSED_AFTER_RETRY,
                _RETRIED_CALLS,
            ),
            (
                _Reply([_cited(BAD_SENTENCE, FIREBALL), "仍然不是句子"]),
                CITATION_FAILED,
                _RETRIED_CALLS,
            ),
        )
        for index, (reply, expected, calls) in enumerate(cases):
            with self.subTest(expected=expected):
                self._assert_audited(index, reply, expected, calls)

    def test_refusal_and_missing_parents_stay_unchecked(self) -> None:
        refused = ElderBrainRag()
        refused.retrieval = _Explode()
        refusal = self._ask(refused, "2024 的武器精通")
        self.assertIn("2024", event_text(refusal))
        self.assertEqual(_stored(self.app.db.path, "2024 的武器精通"), CITATION_UNCHECKED)

        empty = ElderBrainRag()
        empty.retrieval = _EmptySearch()
        empty.reranker = _Keep()
        reply = _Reply()
        empty.generation = reply
        missing = self._ask(empty, "火球术的射程是多少")
        self.assertIn("没有找到足够的原文", event_text(missing))
        self.assertEqual(reply.calls, 0)
        question = "火球术的射程是多少"
        self.assertEqual(_stored(self.app.db.path, question), CITATION_UNCHECKED)

    def _assert_audited(
        self,
        index: int,
        reply: _Reply,
        expected: str,
        calls: int,
    ) -> None:
        rag = ElderBrainRag()
        _ready(rag, reply)
        question = f"火球术射程{index}"
        response = self._ask(rag, question)
        self.assertEqual(response.status, HTTP_OK)
        shown = event_text(response)
        if expected == CITATION_FAILED:
            self.assertIn("没有找到足够的原文", shown)
            self.assertNotIn(BAD_SENTENCE, shown)
        else:
            self.assertIn("150", shown)
        self.assertEqual(reply.calls, calls)
        self.assertEqual(_stored(self.app.db.path, question), expected)

    def _ask(self, rag: ElderBrainRag, question: str) -> object:
        handler = DemoHandler.__new__(DemoHandler)
        handler.rag = rag
        request = make_request(
            "POST",
            PATH_ASK,
            json_bytes({"question": question}),
            self.headers,
        )
        return route_api(self.app, request, handler._answer, None)


def _session_headers(app: AuthApp) -> dict[str, str]:
    moment = now_seconds()
    salt = ip_salt()
    assert salt is not None
    account = app.db.login_email("cite@example.com", hash_ip(TEST_IP, salt), moment)
    assert isinstance(account, Account)
    issued = issue_for(account, moment)
    assert issued is not None
    token, _role = issued
    csrf = "csrf-token-for-tests-0001"
    return {"cookie": cookie_header(token, csrf), "x-csrf-token": csrf}


def _stored(path: Path, question: str) -> str:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT record_json FROM request_audits",
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        record = json.loads(str(row["record_json"]))
        if record["question"] == question:
            return str(record["citation_check"])
    raise AssertionError("没有这条审计")


if __name__ == "__main__":
    unittest.main()
