"""第一期管理端。不连网，不调用嵌入或生成模型。"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from audit_log import NO_EVIDENCE_TEXT
from audit_record import CITATION_FAILED
from auth_app import AuthApp
from auth_constants import (
    HTTP_FORBIDDEN,
    HTTP_OK,
    HTTP_UNAUTHORIZED,
    HTTP_UNPROCESSABLE,
    PATH_ASK,
    PATH_KEYS,
    PATH_QUERY,
)
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
    read_body,
)
from config import SOURCE_ID

PATH_DISABLE = "/api/admin/sources/disable"
PATH_ENABLE = "/api/admin/sources/enable"
PATH_REBUILD = "/api/admin/index/rebuild"
PATH_PUBLISH = "/api/admin/index/publish"
PATH_ROLLBACK = "/api/admin/index/rollback"
PATH_QUEUE = "/api/admin/review-queue"
ADMIN_EMAIL = "admin@example.com"
USER_EMAIL = "reader@example.com"
INDEX_V1 = "idx-2026-11-08-01"
INDEX_V2 = "idx-2026-11-08-02"
MISSING_INDEX = "idx-not-built"
# 和实现里的具名原因一致。差评、术语抽查不进这一期。
REVIEW_REASON_CITATION_FAILED = "引用校验两次失败"
_CSRF = "csrf-token-for-tests-0001"


class _Marked(str):
    """带引用校验结果的回答。页面文本仍是证据不足。"""

    def __new__(cls, text: str, check: str) -> "_Marked":
        value = str.__new__(cls, text)
        value.citation_check = check
        return value


class AdminPhaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = auth_env({"ADMIN_IDENTITIES": f"email:{ADMIN_EMAIL}"})
        self._env.__enter__()
        self._dir = tempfile.TemporaryDirectory()
        self.calls: list[str] = []
        self.build_calls = 0
        self._next_version = INDEX_V1
        self._mark_failed = False
        self.app = AuthApp(
            AuthDb(Path(self._dir.name) / "auth.sqlite"),
            lambda _recipient, _code: None,
            None,
        )

    def tearDown(self) -> None:
        self._dir.cleanup()
        self._env.__exit__(None, None, None)

    def test_guest_and_user_cannot_disable_or_read_queue(self) -> None:
        guest = self._post(PATH_DISABLE, {"source_id": SOURCE_ID}, {})
        self.assertEqual(guest.status, HTTP_UNAUTHORIZED)
        guest_queue = self._get(PATH_QUEUE, {})
        self.assertEqual(guest_queue.status, HTTP_UNAUTHORIZED)
        _user, user_headers = self._session(USER_EMAIL)
        denied = self._post(PATH_DISABLE, {"source_id": SOURCE_ID}, user_headers)
        self.assertEqual(denied.status, HTTP_FORBIDDEN)
        queued = self._get(PATH_QUEUE, user_headers)
        self.assertEqual(queued.status, HTTP_FORBIDDEN)
        rebuild = self._post(PATH_REBUILD, {}, user_headers)
        self.assertEqual(rebuild.status, HTTP_FORBIDDEN)
        _admin, admin_headers = self._session(ADMIN_EMAIL)
        bare = dict(admin_headers)
        bare.pop("x-csrf-token")
        missing_csrf = self._post(PATH_DISABLE, {"source_id": SOURCE_ID}, bare)
        self.assertEqual(missing_csrf.status, HTTP_FORBIDDEN)
        self._ask("没有停用", admin_headers)
        self.assertEqual(self.calls, ["没有停用"])

    def test_disabled_source_does_not_retrieve_or_enqueue(self) -> None:
        _user, user_headers = self._session(USER_EMAIL)
        _admin, admin_headers = self._session(ADMIN_EMAIL)
        self._ask("启用时会检索", user_headers)
        self.assertEqual(self.calls, ["启用时会检索"])
        disabled = self._post(PATH_DISABLE, {"source_id": SOURCE_ID}, admin_headers)
        self.assertEqual(disabled.status, HTTP_OK)
        self.assertFalse(read_body(disabled)["enabled"])
        refused = self._ask("停用后不检索", user_headers)
        self.assertEqual(refused.status, HTTP_OK)
        self.assertEqual(self.calls, ["启用时会检索"])
        self.assertIn("没有找到足够的原文", event_text(refused))
        self._ask("停用后再问", user_headers)
        self.assertEqual(self.calls, ["启用时会检索"])
        created = read_body(self._post(PATH_KEYS, {}, user_headers))
        queried = self._post(
            PATH_QUERY,
            {"question": "接口也停"},
            {"authorization": f"Bearer {created['key']}"},
        )
        self.assertEqual(queried.status, HTTP_OK)
        self.assertEqual(self.calls, ["启用时会检索"])
        dumped = json.dumps(read_body(queried), ensure_ascii=False)
        self.assertIn("没有找到足够的原文", dumped)
        listed = read_body(self._get(PATH_QUEUE, admin_headers))
        self.assertEqual(listed["items"], [])
        enabled = self._post(PATH_ENABLE, {"source_id": SOURCE_ID}, admin_headers)
        self.assertEqual(enabled.status, HTTP_OK)
        self.assertTrue(read_body(enabled)["enabled"])
        self._ask("启用后恢复检索", user_headers)
        self.assertEqual(self.calls[-1], "启用后恢复检索")

    def test_unknown_source_is_not_disabled(self) -> None:
        _admin, headers = self._session(ADMIN_EMAIL)
        bad = self._post(PATH_DISABLE, {"source_id": "src-other"}, headers)
        self.assertEqual(bad.status, HTTP_UNPROCESSABLE)
        self.assertEqual(_admin_rows(self.app.db.path), [])
        self._ask("来源仍在", headers)
        self.assertEqual(self.calls, ["来源仍在"])

    def test_rebuild_does_not_change_published_pointer(self) -> None:
        _admin, headers = self._session(ADMIN_EMAIL)
        self._use_builder(INDEX_V1)
        built = self._post(PATH_REBUILD, {"index_version": "idx-forged"}, headers)
        self.assertEqual(built.status, HTTP_OK)
        body = read_body(built)
        self.assertEqual(body["index_version"], INDEX_V1)
        self.assertIsNone(body["published_index_version"])
        self.assertEqual(_pointer(self.app.db.path), (None, None))
        published = self._post(PATH_PUBLISH, {"index_version": INDEX_V1}, headers)
        self.assertEqual(published.status, HTTP_OK)
        self._use_builder(INDEX_V2)
        again = self._post(PATH_REBUILD, {}, headers)
        self.assertEqual(again.status, HTTP_OK)
        rebuilt = read_body(again)
        self.assertEqual(rebuilt["index_version"], INDEX_V2)
        self.assertEqual(rebuilt["published_index_version"], INDEX_V1)
        self.assertEqual(_pointer(self.app.db.path), (INDEX_V1, None))
        self.assertEqual(_builds(self.app.db.path), {INDEX_V1, INDEX_V2})
        self.assertEqual(self.build_calls, 2)
        self._ask("重建后仍用旧版", headers)
        stored = _index_version(self.app.db.path, "重建后仍用旧版")
        self.assertEqual(stored, INDEX_V1)

    def test_publish_and_rollback_switch_ask_version(self) -> None:
        _admin, headers = self._session(ADMIN_EMAIL)
        self._use_builder(INDEX_V1)
        self._post(PATH_REBUILD, {}, headers)
        self._ask("还没发布", headers)
        self.assertEqual(_index_version(self.app.db.path, "还没发布"), SOURCE_ID)
        self._post(PATH_PUBLISH, {"index_version": INDEX_V1}, headers)
        self._ask("用第一版", headers)
        self.assertEqual(_index_version(self.app.db.path, "用第一版"), INDEX_V1)
        self._use_builder(INDEX_V2)
        self._post(PATH_REBUILD, {}, headers)
        self._post(PATH_PUBLISH, {"index_version": INDEX_V2}, headers)
        self._ask("用第二版", headers)
        self.assertEqual(_index_version(self.app.db.path, "用第二版"), INDEX_V2)
        self.assertEqual(self.calls, ["还没发布", "用第一版", "用第二版"])
        rolled = self._post(PATH_ROLLBACK, {}, headers)
        self.assertEqual(rolled.status, HTTP_OK)
        self.assertEqual(read_body(rolled)["published_index_version"], INDEX_V1)
        self._ask("回到第一版", headers)
        self.assertEqual(_index_version(self.app.db.path, "回到第一版"), INDEX_V1)
        self.assertEqual(_builds(self.app.db.path), {INDEX_V1, INDEX_V2})
        self.assertEqual(_pointer(self.app.db.path), (INDEX_V1, None))

    def test_missing_version_keeps_pointer(self) -> None:
        _admin, headers = self._session(ADMIN_EMAIL)
        missing = self._post(PATH_PUBLISH, {"index_version": INDEX_V2}, headers)
        self.assertEqual(missing.status, HTTP_UNPROCESSABLE)
        early = self._post(PATH_ROLLBACK, {}, headers)
        self.assertEqual(early.status, HTTP_UNPROCESSABLE)
        self.assertEqual(_pointer(self.app.db.path), (None, None))
        self._use_builder(INDEX_V1)
        self._post(PATH_REBUILD, {}, headers)
        self._post(PATH_PUBLISH, {"index_version": INDEX_V1}, headers)
        bad = self._post(PATH_PUBLISH, {"index_version": MISSING_INDEX}, headers)
        self.assertEqual(bad.status, HTTP_UNPROCESSABLE)
        nowhere = self._post(PATH_ROLLBACK, {}, headers)
        self.assertEqual(nowhere.status, HTTP_UNPROCESSABLE)
        self.assertEqual(_pointer(self.app.db.path), (INDEX_V1, None))
        self._ask("指针没动", headers)
        self.assertEqual(_index_version(self.app.db.path, "指针没动"), INDEX_V1)

    def test_citation_failure_enters_review_queue(self) -> None:
        _user, user_headers = self._session(USER_EMAIL)
        _admin, admin_headers = self._session(ADMIN_EMAIL)
        question = "引用对不上的火球术"
        self._mark_failed = True
        asked = self._ask(question, user_headers)
        self.assertEqual(asked.status, HTTP_OK)
        self.assertIn("没有找到足够的原文", event_text(asked))
        self.assertEqual(self.calls, [question])
        hidden = self._get(PATH_QUEUE, user_headers)
        self.assertEqual(hidden.status, HTTP_FORBIDDEN)
        self.assertNotIn(question, hidden.body.decode("utf-8"))
        listed = read_body(self._get(PATH_QUEUE, admin_headers))
        items = listed["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["reason"], REVIEW_REASON_CITATION_FAILED)
        self.assertEqual(items[0]["question"], question)
        self.assertTrue(items[0]["created_at"])
        self.assertNotIn(USER_EMAIL, json.dumps(items[0], ensure_ascii=False))

    def test_admin_audit_records_actor_and_action(self) -> None:
        admin, headers = self._session(ADMIN_EMAIL)
        self._use_builder(INDEX_V1)
        self._post(PATH_DISABLE, {"source_id": SOURCE_ID}, headers)
        self._post(PATH_ENABLE, {"source_id": SOURCE_ID}, headers)
        self._post(PATH_REBUILD, {}, headers)
        self._post(PATH_PUBLISH, {"index_version": INDEX_V1}, headers)
        self._use_builder(INDEX_V2)
        self._post(PATH_REBUILD, {}, headers)
        self._post(PATH_PUBLISH, {"index_version": INDEX_V2}, headers)
        rolled = self._post(PATH_ROLLBACK, {}, headers)
        self.assertEqual(rolled.status, HTTP_OK)
        rows = _admin_rows(self.app.db.path)
        actions = [str(row["action"]) for row in rows]
        self.assertEqual(
            actions,
            ["停用来源", "启用来源", "重建索引", "发布", "重建索引", "发布", "回退"],
        )
        for row in rows:
            self.assertEqual(row["actor_id"], admin.user_id)
            self.assertNotIn("@", str(row["actor_id"]))
            self.assertTrue(row["created_at"])
            self.assertNotIn(ADMIN_EMAIL, json.dumps(dict(row), ensure_ascii=False))

    def _use_builder(self, version: str) -> None:
        self._next_version = version
        self.app.index_builder = self._build

    def _build(self) -> str:
        """测试注入的构建。只交出版本号，不读向量。"""
        self.build_calls += 1
        return self._next_version

    def _ask(self, question: str, headers: dict[str, str]) -> object:
        return self._post(PATH_ASK, {"question": question}, headers)

    def _session(self, email: str) -> tuple[Account, dict[str, str]]:
        moment = now_seconds()
        salt = ip_salt()
        assert salt is not None
        account = self.app.db.login_email(email, hash_ip(TEST_IP, salt), moment)
        assert isinstance(account, Account)
        issued = issue_for(account, moment)
        assert issued is not None
        token, _role = issued
        headers = {"cookie": cookie_header(token, _CSRF), "x-csrf-token": _CSRF}
        return account, headers

    def _post(
        self,
        path: str,
        payload: dict[str, object],
        headers: dict[str, str],
    ) -> object:
        request = make_request("POST", path, json_bytes(payload), headers)
        return route_api(self.app, request, self._answer, None)

    def _get(self, path: str, headers: dict[str, str]) -> object:
        request = make_request("GET", path, b"", headers)
        return route_api(self.app, request, self._answer, None)

    def _answer(self, question: str, _card: object) -> str:
        self.calls.append(question)
        if self._mark_failed:
            return _Marked(NO_EVIDENCE_TEXT, CITATION_FAILED)
        return "规则回答"


def _pointer(path: Path) -> tuple[str | None, str | None]:
    row = _one(
        path,
        "SELECT index_version, previous_version FROM published_pointer",
    )
    if row is None:
        return None, None
    previous = row["previous_version"]
    published = None if row["index_version"] is None else str(row["index_version"])
    return published, None if previous is None else str(previous)


def _builds(path: Path) -> set[str]:
    rows = _all(path, "SELECT index_version FROM index_builds")
    return {str(row["index_version"]) for row in rows}


def _admin_rows(path: Path) -> list[sqlite3.Row]:
    return _all(
        path,
        "SELECT actor_id, created_at, action FROM admin_audits ORDER BY audit_id",
    )


def _index_version(path: Path, question: str) -> str:
    rows = _all(path, "SELECT record_json FROM request_audits")
    for row in rows:
        record = json.loads(str(row["record_json"]))
        if record.get("question") == question:
            return str(record["index_version"])
    raise AssertionError("没有这条提问审计")


def _all(path: Path, sql: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return list(conn.execute(sql).fetchall())
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _one(path: Path, sql: str) -> sqlite3.Row | None:
    found = _all(path, sql)
    if not found:
        return None
    return found[0]


if __name__ == "__main__":
    unittest.main()
