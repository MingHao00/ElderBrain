"""生成费用记入审计和当日累计。不调用真实模型，也不连接 SMTP。"""

import io
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import alert_mail
from audit_record import FIELD_COST_ESTIMATE, FIELD_TOKENS_IN, FIELD_TOKENS_OUT
from auth_app import AuthApp
from auth_constants import (
    ERR_ANSWER_FAILED,
    HTTP_OK,
    HTTP_SERVER_ERROR,
    HTTP_TOO_MANY,
    MICRO_PER_YUAN,
    MINUTE_WINDOW_SECONDS,
    PATH_ASK,
    SHANGHAI_TZ,
    SITE_DAILY_ALERT_MICRO,
    SITE_DAILY_CAP_MICRO,
    SITE_DAILY_CAP_YUAN,
)
from auth_db import Account, AuthDb
from auth_http import Outgoing
from auth_identity import hash_ip, ip_salt
from auth_quota import Reserved, reserve_quota, settle_cost, yuan_to_micro
from auth_routes import route_api
from auth_session import issue_for, now_seconds
from auth_testkit import (
    TEST_IP,
    auth_env,
    cookie_header,
    json_bytes,
    make_request,
    read_body,
)
from demo_server import DemoHandler
from generation_call import qwen_yuan
from modules.generation import NO_EVIDENCE, GenerationIntegrationModule
from system import ElderBrainRag
from test_system import (
    BAD_SENTENCE,
    FIREBALL,
    SOURCE_TEXT,
    _EmptySearch,
    _Explode,
    _Hit,
    _Keep,
    _cited,
    _parent_piece,
)

# 形状像令牌，但不是任何服务的密钥。失败日志里不能出现它。
_FAKE_TOKEN = "sk-placeholder-not-a-real-key"
_SMTP_PASSWORD = "unit-test-smtp-secret"
_ALERT_ENV = {
    "ALERT_SMTP_HOST": "smtp.example.com",
    "ALERT_SMTP_PORT": "587",
    "ALERT_SMTP_USER": "alert-user",
    "ALERT_SMTP_PASSWORD": _SMTP_PASSWORD,
    "ALERT_MAIL_FROM": "alert@example.com",
    "ALERT_MAIL_TO": "admin@example.com",
}
# 低档单价下，这一对 token 的费用不是整数元。
_PROMPT_TOKENS = 1_000
_COMPLETION_TOKENS = 500
_RETRY_PROMPT = 250
_RETRY_COMPLETION = 80
# 输出 0.8 元/百万。这个数量跨过 16.8 元，又不到 21 元。
_ALERT_PROMPT = 1
_ALERT_COMPLETION = 21_000_000
# 还差 1 微元到全站上限。这一问会跨过去，但仍然返回答案。
_MICRO_SHORT_OF_CAP = 1
_QUESTION = "火球术的射程是多少"
_REFUSAL_QUESTION = "2024 的武器精通"
_ZONE = ZoneInfo(SHANGHAI_TZ)
# 错开滑动窗口，第二次才会落到同一日的费用，而不是每分钟上限。
_MINUTE_GAP = MINUTE_WINDOW_SECONDS + 1


class AskCostTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = auth_env(_ALERT_ENV)
        self._env.__enter__()
        self._dir = tempfile.TemporaryDirectory()
        self.sent: list[tuple[str, str]] = []
        self.app = AuthApp(
            AuthDb(Path(self._dir.name) / "auth.sqlite"),
            lambda _recipient, _code: None,
            None,
            self._deliver,
        )
        self.account, self.headers = _session(self.app)

    def tearDown(self) -> None:
        self._dir.cleanup()
        self._env.__exit__(None, None, None)

    def test_one_call_books_qwen_price(self) -> None:
        usage = _Usage(_PROMPT_TOKENS, _COMPLETION_TOKENS)
        rag, client = _priced_rag([_response(_cited(SOURCE_TEXT, FIREBALL), usage)])
        response = self._ask(rag, _QUESTION)
        self.assertEqual(response.status, HTTP_OK)
        self.assertEqual(client.calls, 1)
        expected = qwen_yuan(_PROMPT_TOKENS, _COMPLETION_TOKENS)
        record = _audit(self.app.db.path, _QUESTION)
        self.assertEqual(record[FIELD_TOKENS_IN], _PROMPT_TOKENS)
        self.assertEqual(record[FIELD_TOKENS_OUT], _COMPLETION_TOKENS)
        self.assertAlmostEqual(float(record[FIELD_COST_ESTIMATE]), expected)
        self.assertEqual(_spend(self.app.db.path), round(expected * MICRO_PER_YUAN))
        self.assertEqual(self.sent, [])

    def test_retry_adds_both_calls(self) -> None:
        first = _Usage(_PROMPT_TOKENS, _COMPLETION_TOKENS)
        second = _Usage(_RETRY_PROMPT, _RETRY_COMPLETION)
        rag, client = _priced_rag([
            _response("不是引用句子", first),
            _response(_cited(SOURCE_TEXT, FIREBALL), second),
        ])
        response = self._ask(rag, _QUESTION)
        self.assertEqual(response.status, HTTP_OK)
        self.assertEqual(client.calls, 2)
        first_yuan = qwen_yuan(_PROMPT_TOKENS, _COMPLETION_TOKENS)
        second_yuan = qwen_yuan(_RETRY_PROMPT, _RETRY_COMPLETION)
        record = _audit(self.app.db.path, _QUESTION)
        self.assertEqual(record[FIELD_TOKENS_IN], _PROMPT_TOKENS + _RETRY_PROMPT)
        self.assertEqual(record[FIELD_TOKENS_OUT], _COMPLETION_TOKENS + _RETRY_COMPLETION)
        self.assertAlmostEqual(float(record[FIELD_COST_ESTIMATE]), first_yuan + second_yuan)
        micro = round(first_yuan * MICRO_PER_YUAN) + round(second_yuan * MICRO_PER_YUAN)
        self.assertEqual(_spend(self.app.db.path), micro)

    def test_two_failures_still_add_both_calls(self) -> None:
        first = _Usage(_PROMPT_TOKENS, _COMPLETION_TOKENS)
        second = _Usage(_RETRY_PROMPT, _RETRY_COMPLETION)
        rag, client = _priced_rag([
            _response(_cited(BAD_SENTENCE, FIREBALL), first),
            _response("仍然不是句子", second),
        ])
        response = self._ask(rag, _QUESTION)
        self.assertEqual(response.status, HTTP_OK)
        self.assertEqual(str(read_body(response)["answer"]), NO_EVIDENCE)
        self.assertEqual(client.calls, 2)
        record = _audit(self.app.db.path, _QUESTION)
        self.assertEqual(record[FIELD_TOKENS_IN], _PROMPT_TOKENS + _RETRY_PROMPT)
        self.assertEqual(record[FIELD_TOKENS_OUT], _COMPLETION_TOKENS + _RETRY_COMPLETION)
        first_yuan = qwen_yuan(_PROMPT_TOKENS, _COMPLETION_TOKENS)
        second_yuan = qwen_yuan(_RETRY_PROMPT, _RETRY_COMPLETION)
        micro = round(first_yuan * MICRO_PER_YUAN) + round(second_yuan * MICRO_PER_YUAN)
        self.assertEqual(_spend(self.app.db.path), micro)

    def test_missing_usage_keeps_the_answer(self) -> None:
        rag, client = _priced_rag([_bare(_cited(SOURCE_TEXT, FIREBALL))])
        response = self._ask(rag, _QUESTION)
        self.assertEqual(response.status, HTTP_OK)
        self.assertEqual(client.calls, 1)
        self.assertIn(SOURCE_TEXT, str(read_body(response)["answer"]))
        record = _audit(self.app.db.path, _QUESTION)
        self.assertEqual(record[FIELD_TOKENS_IN], 0)
        self.assertEqual(record[FIELD_TOKENS_OUT], 0)
        self.assertEqual(record[FIELD_COST_ESTIMATE], 0)
        self.assertEqual(_spend(self.app.db.path), 0)

    def test_skipped_generation_is_free(self) -> None:
        refused = ElderBrainRag()
        refused.retrieval = _Explode()
        first = self._ask(refused, _REFUSAL_QUESTION)
        self.assertEqual(first.status, HTTP_OK)
        empty = ElderBrainRag()
        empty.retrieval = _EmptySearch()
        empty.reranker = _Keep()
        second = self._ask(empty, _QUESTION)
        self.assertEqual(second.status, HTTP_OK)
        self.assertEqual(str(read_body(second)["answer"]), NO_EVIDENCE)
        self.assertEqual(_spend(self.app.db.path), 0)
        for question in (_REFUSAL_QUESTION, _QUESTION):
            record = _audit(self.app.db.path, question)
            self.assertEqual(record[FIELD_TOKENS_IN], 0)
            self.assertEqual(record[FIELD_TOKENS_OUT], 0)
            self.assertEqual(record[FIELD_COST_ESTIMATE], 0)

    def test_cap_blocks_before_generation(self) -> None:
        self._reserve(now_seconds(), yuan_to_micro(SITE_DAILY_CAP_YUAN))
        rag, client = _priced_rag([_response(_cited(SOURCE_TEXT, FIREBALL), _small_usage())])
        response = self._ask(rag, _QUESTION)
        self.assertEqual(response.status, HTTP_TOO_MANY)
        self.assertIn("全站上限", str(read_body(response)["error"]))
        self.assertEqual(client.calls, 0)
        self.assertEqual(self.sent, [])

    def test_crossing_cap_still_answers_then_blocks(self) -> None:
        self._reserve(now_seconds(), SITE_DAILY_CAP_MICRO - _MICRO_SHORT_OF_CAP)
        rag, client = _priced_rag([
            _response(_cited(SOURCE_TEXT, FIREBALL), _small_usage()),
            _response(_cited(SOURCE_TEXT, FIREBALL), _small_usage()),
        ])
        allowed = self._ask(rag, _QUESTION)
        self.assertEqual(allowed.status, HTTP_OK)
        self.assertEqual(client.calls, 1)
        blocked = self._ask(rag, "第二次提问")
        self.assertEqual(blocked.status, HTTP_TOO_MANY)
        self.assertIn("全站上限", str(read_body(blocked)["error"]))
        self.assertEqual(client.calls, 1)

    def test_crossing_eighty_percent_sends_one_mail(self) -> None:
        usage = _Usage(_ALERT_PROMPT, _ALERT_COMPLETION)
        expected = qwen_yuan(_ALERT_PROMPT, _ALERT_COMPLETION)
        alert_micro = round(expected * MICRO_PER_YUAN)
        self.assertGreaterEqual(alert_micro, SITE_DAILY_ALERT_MICRO)
        self.assertLess(alert_micro, SITE_DAILY_CAP_MICRO)
        rag, client = _priced_rag([
            _response(_cited(SOURCE_TEXT, FIREBALL), usage),
            _response(_cited(SOURCE_TEXT, FIREBALL), _small_usage()),
        ])
        first = self._ask(rag, _QUESTION)
        self.assertEqual(first.status, HTTP_OK)
        self.assertEqual(len(self.sent), 1)
        subject, body = self.sent[0]
        self.assertIn("80%", subject)
        self.assertIn("80%", body)
        self._assert_mail_has_no_secrets(subject, body)
        second = self._ask(rag, "同一天的下一问")
        self.assertEqual(second.status, HTTP_OK)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(client.calls, 2)

    def test_mail_failure_keeps_the_answer(self) -> None:
        self.app.alert_deliver = self._boom
        self.attempts = 0
        usage = _Usage(_ALERT_PROMPT, _ALERT_COMPLETION)
        rag, _client = _priced_rag([
            _response(_cited(SOURCE_TEXT, FIREBALL), usage),
            _response(_cited(SOURCE_TEXT, FIREBALL), _small_usage()),
        ])
        buffer = io.StringIO()
        with self.assertLogs(alert_mail.__name__, level="ERROR") as logs:
            with redirect_stderr(buffer):
                response = self._ask(rag, _QUESTION)
        self.assertEqual(response.status, HTTP_OK)
        self.assertIn(SOURCE_TEXT, str(read_body(response)["answer"]))
        self.assertEqual(self.attempts, 1)
        later = self._ask(rag, "发信失败后的下一问")
        self.assertEqual(later.status, HTTP_OK)
        self.assertEqual(self.attempts, 1)
        combined = buffer.getvalue() + "\n" + "\n".join(logs.output)
        self.assertNotIn(_SMTP_PASSWORD, combined)
        self.assertNotIn(_FAKE_TOKEN, combined)
        self.assertNotIn(_QUESTION, combined)
        self.assertNotIn("admin@example.com", combined)

    def test_model_error_releases_without_cost_or_alert(self) -> None:
        seeded = _PROMPT_TOKENS
        self._reserve(now_seconds(), seeded)
        client = _BoomClient()
        rag = ElderBrainRag()
        rag.retrieval = _Hit()
        rag.reranker = _Keep()
        rag.generation = GenerationIntegrationModule(0, 32, client, "test-model")
        rag.data.parents = {"fire": _parent_piece()}
        response = self._ask(rag, _QUESTION)
        self.assertEqual(response.status, HTTP_SERVER_ERROR)
        self.assertEqual(read_body(response)["error"], ERR_ANSWER_FAILED)
        self.assertNotIn(_FAKE_TOKEN, response.body.decode("utf-8"))
        self.assertEqual(client.calls, 1)
        self.assertEqual(_spend(self.app.db.path), seeded)
        self.assertEqual(self.sent, [])

    def test_parallel_crossings_alert_once(self) -> None:
        moment = _shanghai(2026, 11, 7, 12, 0)
        reserved = [self._reserve(moment) for _index in range(2)]
        results: list[bool] = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def worker(event_id: int) -> None:
            barrier.wait()
            alert = settle_cost(self.app.db, event_id, SITE_DAILY_ALERT_MICRO)
            with lock:
                results.append(alert)

        threads = [
            threading.Thread(target=worker, args=(item.event_id,))
            for item in reserved
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results.count(True), 1)

    def test_next_shanghai_day_can_alert_again(self) -> None:
        first_day = _shanghai(2026, 11, 7, 12, 0)
        first = self._reserve(first_day)
        later = self._reserve(first_day + _MINUTE_GAP)
        second = self._reserve(_shanghai(2026, 11, 8, 12, 0))
        self.assertTrue(settle_cost(self.app.db, first.event_id, SITE_DAILY_ALERT_MICRO))
        self.assertFalse(settle_cost(self.app.db, later.event_id, _MICRO_SHORT_OF_CAP))
        self.assertTrue(settle_cost(self.app.db, second.event_id, SITE_DAILY_ALERT_MICRO))

    def _deliver(self, subject: str, body: str) -> None:
        self.sent.append((subject, body))

    def _boom(self, _subject: str, _body: str) -> None:
        self.attempts += 1
        raise OSError(f"rejected {_SMTP_PASSWORD}")

    def _ask(self, rag: ElderBrainRag, question: str) -> Outgoing:
        handler = DemoHandler.__new__(DemoHandler)
        handler.rag = rag
        request = make_request(
            "POST",
            PATH_ASK,
            json_bytes({"question": question}),
            self.headers,
        )
        return route_api(self.app, request, handler._answer, None)

    def _reserve(self, moment: int, cost_micro: int = 0) -> Reserved:
        result = reserve_quota(
            self.app.db,
            self.account.user_id,
            self.account.created_at,
            cost_micro,
            moment,
        )
        self.assertIsInstance(result, Reserved)
        assert isinstance(result, Reserved)
        return result

    def _assert_mail_has_no_secrets(self, subject: str, body: str) -> None:
        text = subject + body
        self.assertNotIn(_QUESTION, text)
        self.assertNotIn("@", text)
        self.assertNotIn(_SMTP_PASSWORD, text)
        self.assertNotIn("sk-", text)


class _Usage:
    def __init__(self, prompt: int, completion: int) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Message(content)


class _Response:
    def __init__(self, content: str, usage: object | None) -> None:
        self.choices = [_Choice(content)]
        self.usage = usage


class _Bare:
    """没有 usage 属性。不能因此把回答变成 500。"""

    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]


class _Completions:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls = 0

    def create(
        self,
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        messages: list[dict[str, str]],
        extra_body: dict[str, object],
    ) -> object:
        del model, temperature, max_tokens, messages, extra_body
        self.calls += 1
        if self.calls > len(self.responses):
            raise AssertionError("不应再生成")
        return self.responses[self.calls - 1]


class _Chat:
    def __init__(self, completions: _Completions) -> None:
        self.completions = completions


class _Client:
    def __init__(self, responses: list[object]) -> None:
        self.chat = _Chat(_Completions(responses))

    @property
    def calls(self) -> int:
        return self.chat.completions.calls


class _BoomClient:
    """对话接口直接抛错。调用方应退回配额，不能把异常正文返回给页面。"""

    def __init__(self) -> None:
        self.calls = 0
        self.chat = self

    @property
    def completions(self) -> "_BoomClient":
        return self

    def create(
        self,
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        messages: list[dict[str, str]],
        extra_body: dict[str, object],
    ) -> object:
        del model, temperature, max_tokens, messages, extra_body
        self.calls += 1
        raise RuntimeError(f"model down {_FAKE_TOKEN}")


def _response(content: str, usage: _Usage) -> _Response:
    return _Response(content, usage)


def _bare(content: str) -> _Bare:
    return _Bare(content)


def _small_usage() -> _Usage:
    return _Usage(_PROMPT_TOKENS, _COMPLETION_TOKENS)


def _priced_rag(responses: list[object]) -> tuple[ElderBrainRag, _Client]:
    client = _Client(responses)
    rag = ElderBrainRag()
    rag.retrieval = _Hit()
    rag.reranker = _Keep()
    rag.generation = GenerationIntegrationModule(0, 32, client, "test-model")
    rag.data.parents = {"fire": _parent_piece()}
    return rag, client


def _session(app: AuthApp) -> tuple[Account, dict[str, str]]:
    moment = now_seconds()
    salt = ip_salt()
    assert salt is not None
    account = app.db.login_email("cost@example.com", hash_ip(TEST_IP, salt), moment)
    assert isinstance(account, Account)
    issued = issue_for(account, moment)
    assert issued is not None
    token, _role = issued
    csrf = "csrf-token-for-tests-0001"
    headers = {"cookie": cookie_header(token, csrf), "x-csrf-token": csrf}
    return account, headers


def _audit(path: Path, question: str) -> dict[str, object]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT record_json FROM request_audits").fetchall()
    finally:
        conn.close()
    for row in rows:
        if row["record_json"] is None:
            continue
        record = json.loads(str(row["record_json"]))
        if record.get("question") == question:
            return record
    raise AssertionError("没有这条审计")


def _spend(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_micro), 0) FROM quota_uses WHERE released = 0",
        ).fetchone()
    finally:
        conn.close()
    return int(row[0])


def _shanghai(year: int, month: int, day: int, hour: int, minute: int) -> int:
    local = datetime(year, month, day, hour, minute, tzinfo=_ZONE)
    return int(local.timestamp())
