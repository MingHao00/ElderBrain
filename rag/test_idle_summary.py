"""闲置摘要。用短时钟调用扫描，不连网，也不睡满 30 分钟。"""

import json
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from auth_constants import (
    MICRO_PER_YUAN,
    MINUTE_LIMIT,
    MINUTE_WINDOW_SECONDS,
    NEW_ACCOUNT_DAILY_LIMIT,
    QUOTA_DAILY,
    QUOTA_MINUTE,
    SHANGHAI_TZ,
    SITE_DAILY_ALERT_MICRO,
    SITE_DAILY_CAP_MICRO,
    SITE_DAILY_CAP_YUAN,
    USE_KIND_ASK,
    USE_KIND_IDLE,
)
from auth_db import Account, AuthDb
from auth_identity import hash_ip, ip_salt
from auth_quota import (
    QuotaDeny,
    Reserved,
    open_uses,
    record_idle_cost,
    reserve_quota,
    yuan_to_micro,
)
from auth_testkit import TEST_IP, auth_env
from config import RULE_VERSION
from conversation_store import commit_turn, list_conversations, load_card
from generation_call import qwen_yuan
from idle_summary import (
    IDLE_SUMMARY_SECONDS,
    IdleBrief,
    parse_idle_summary,
    scan_idle_summaries,
    start_idle_scan,
    summarize_with_qwen,
)
from session_card import CardEntry, SessionCard

_ZONE = ZoneInfo(SHANGHAI_TZ)
_GAP = MINUTE_WINDOW_SECONDS + 1
_JOIN_SECONDS = 2
# 低档单价。费用不是整数元时仍要按微元四舍五入。
_PROMPT_TOKENS = 1000
_COMPLETION_TOKENS = 500
# 输出单价下跨过 16.8 元，又不到 21 元。
_ALERT_PROMPT = 1
_ALERT_COMPLETION = 21_000_000
_QUESTION = "火球术怎么施放"
_FOLLOW = "升环呢"
_ASSISTANT = "只有助手原文才会出现的句子。火球术伤害是 8d6。"
_CONCLUSION = "火球术伤害是 8d6，这是结论不应该进卡片。"
_ENTRY = "srd51:spell:fireball"
_CONCEPT = "concept.fireball.card"
_TITLE = "卡片标题火球术"
_NEW_ENTRY = "srd51:spell:shield"
_NEW_CONCEPT = "concept.shield"
_NEW_TITLE = "护盾术"
_FAKE_USER = "site"


class IdleSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = auth_env()
        self._env.__enter__()
        self._dir = tempfile.TemporaryDirectory()
        self.db = AuthDb(Path(self._dir.name) / "auth.sqlite")
        self.account = self._user("idle@example.com", _at(2026, 11, 1, 8, 0))

    def tearDown(self) -> None:
        self._dir.cleanup()
        self._env.__exit__(None, None, None)

    def test_idle_window_defaults_to_thirty_minutes(self) -> None:
        self.assertEqual(IDLE_SUMMARY_SECONDS, 30 * 60)

    def test_before_thirty_minutes_does_not_call(self) -> None:
        asked = _at(2026, 11, 8, 12, 0)
        conversation_id = self._talk(asked)
        calls: list[tuple[str, ...]] = []

        def summarize(
            utterances: tuple[str, ...],
            card: SessionCard,
        ) -> IdleBrief | None:
            del card
            calls.append(utterances)
            return _brief()

        found = scan_idle_summaries(
            self.db,
            summarize,
            asked + IDLE_SUMMARY_SECONDS - 1,
        )
        self.assertEqual(found, 0)
        self.assertEqual(calls, [])
        self.assertEqual(_folded_users(self.db.path, conversation_id), [0])
        self.assertEqual(load_card(self.db, conversation_id).entries[0].entry_id, _ENTRY)

    def test_due_unfolded_user_text_replaces_card_with_entries(self) -> None:
        asked = _at(2026, 11, 8, 12, 0)
        conversation_id = self._talk(asked)
        seen: list[tuple[tuple[str, ...], SessionCard]] = []

        def summarize(
            utterances: tuple[str, ...],
            card: SessionCard,
        ) -> IdleBrief | None:
            seen.append((utterances, card))
            return IdleBrief(
                (CardEntry(_NEW_ENTRY, _NEW_CONCEPT, _NEW_TITLE),),
                "5.1",
                _PROMPT_TOKENS,
                _COMPLETION_TOKENS,
            )

        found = scan_idle_summaries(self.db, summarize, asked + IDLE_SUMMARY_SECONDS)
        self.assertEqual(found, 1)
        self.assertEqual(len(seen), 1)
        utterances, card = seen[0]
        self.assertEqual(utterances, (_QUESTION,))
        self.assertNotIn(_ASSISTANT, utterances)
        self.assertEqual(card.entries[0].entry_id, _ENTRY)
        stored = load_card(self.db, conversation_id)
        self.assertEqual(stored.rule_version, "5.1")
        self.assertEqual(len(stored.entries), 1)
        self.assertEqual(stored.entries[0].entry_id, _NEW_ENTRY)
        self.assertEqual(stored.entries[0].concept_key, _NEW_CONCEPT)
        self.assertEqual(stored.entries[0].title, _NEW_TITLE)
        raw = _card_json(self.db.path, conversation_id)
        self.assertNotIn(_CONCLUSION, raw)
        self.assertNotIn("8d6", raw)
        self.assertEqual(_folded_users(self.db.path, conversation_id), [1])
        self.assertEqual(_folded_assistants(self.db.path, conversation_id), [0])
        again = scan_idle_summaries(self.db, summarize, asked + IDLE_SUMMARY_SECONDS)
        self.assertEqual(again, 0)
        self.assertEqual(len(seen), 1)

    def test_short_idle_seconds_do_not_sleep(self) -> None:
        asked = _at(2026, 11, 8, 9, 0)
        self._talk(asked)
        calls: list[int] = []

        def summarize(
            utterances: tuple[str, ...],
            card: SessionCard,
        ) -> IdleBrief | None:
            del utterances, card
            calls.append(1)
            return _brief()

        started = time.monotonic()
        scan_idle_summaries(self.db, summarize, asked + 5, idle_seconds=5)
        self.assertEqual(calls, [1])
        self.assertLess(time.monotonic() - started, _JOIN_SECONDS)

    def test_assistant_timestamp_does_not_reset_the_timer(self) -> None:
        asked = _at(2026, 11, 8, 12, 0)
        conversation_id = self._talk(asked)
        _insert_message(
            self.db.path,
            conversation_id,
            "assistant",
            _ASSISTANT,
            asked + IDLE_SUMMARY_SECONDS,
        )
        calls: list[tuple[str, ...]] = []

        def summarize(
            utterances: tuple[str, ...],
            card: SessionCard,
        ) -> IdleBrief | None:
            del card
            calls.append(utterances)
            return _brief()

        scan_idle_summaries(self.db, summarize, asked + IDLE_SUMMARY_SECONDS)
        self.assertEqual(calls, [(_QUESTION,)])

    def test_idle_cost_skips_user_counts_and_adds_site_spend(self) -> None:
        asked = _at(2026, 11, 8, 13, 0)
        self._talk(asked)
        moment = asked + IDLE_SUMMARY_SECONDS

        def summarize(
            utterances: tuple[str, ...],
            card: SessionCard,
        ) -> IdleBrief | None:
            del utterances, card
            return _brief()

        scan_idle_summaries(self.db, summarize, moment)
        expected = round(qwen_yuan(_PROMPT_TOKENS, _COMPLETION_TOKENS) * MICRO_PER_YUAN)
        self.assertEqual(open_uses(self.db, self.account.user_id), 0)
        self.assertEqual(_spend(self.db.path), expected)
        kind, owner = _idle_charge(self.db.path)
        self.assertEqual(kind, USE_KIND_IDLE)
        self.assertEqual(owner, self.account.user_id)
        self.assertNotEqual(owner, _FAKE_USER)
        reserved = reserve_quota(
            self.db,
            self.account.user_id,
            self.account.created_at,
            0,
            moment,
        )
        self.assertIsInstance(reserved, Reserved)
        self.assertEqual(open_uses(self.db, self.account.user_id), 1)

    def test_minute_count_skips_idle_rows(self) -> None:
        asked = _at(2026, 11, 8, 14, 0)
        self._talk(asked)
        moment = asked + IDLE_SUMMARY_SECONDS
        scan_idle_summaries(self.db, _always, moment)
        allowed = _count_allowed(self.db, self.account, moment, MINUTE_LIMIT + 1, 0)
        self.assertEqual(allowed, MINUTE_LIMIT)

    def test_daily_count_skips_idle_rows(self) -> None:
        # 注册未满一天才是 20 次。老账号的 100 次看不出闲置行有没有被算进去。
        created = _at(2026, 11, 8, 15, 0)
        account = self._user("new-idle@example.com", created)
        self._talk(created, account)
        moment = created + IDLE_SUMMARY_SECONDS
        scan_idle_summaries(self.db, _always, moment)
        allowed = _count_allowed(
            self.db,
            account,
            moment,
            NEW_ACCOUNT_DAILY_LIMIT + 1,
            _GAP,
        )
        self.assertEqual(allowed, NEW_ACCOUNT_DAILY_LIMIT)
        denied = reserve_quota(
            self.db,
            account.user_id,
            account.created_at,
            0,
            moment + NEW_ACCOUNT_DAILY_LIMIT * _GAP,
        )
        self.assertIsInstance(denied, QuotaDeny)
        assert isinstance(denied, QuotaDeny)
        self.assertEqual(denied.kind, QUOTA_DAILY)

    def test_missing_usage_records_zero_and_still_folds(self) -> None:
        asked = _at(2026, 11, 8, 16, 0)
        conversation_id = self._talk(asked)

        def summarize(
            utterances: tuple[str, ...],
            card: SessionCard,
        ) -> IdleBrief | None:
            del utterances, card
            return IdleBrief((CardEntry(_NEW_ENTRY, _NEW_CONCEPT, _NEW_TITLE),), "5.1")

        scan_idle_summaries(self.db, summarize, asked + IDLE_SUMMARY_SECONDS)
        self.assertEqual(_spend(self.db.path), 0)
        self.assertEqual(open_uses(self.db, self.account.user_id), 0)
        self.assertEqual(_folded_users(self.db.path, conversation_id), [1])
        self.assertEqual(load_card(self.db, conversation_id).entries[0].entry_id, _NEW_ENTRY)

    def test_missing_rule_version_keeps_the_card(self) -> None:
        asked = _at(2026, 11, 8, 17, 0)
        conversation_id = self._talk(asked)
        _set_rule_version(self.db.path, conversation_id, RULE_VERSION)

        def summarize(
            utterances: tuple[str, ...],
            card: SessionCard,
        ) -> IdleBrief | None:
            del utterances, card
            return IdleBrief((CardEntry(_NEW_ENTRY, None, _NEW_TITLE),), None, 1, 1)

        scan_idle_summaries(self.db, summarize, asked + IDLE_SUMMARY_SECONDS)
        self.assertEqual(load_card(self.db, conversation_id).rule_version, RULE_VERSION)

    def test_returned_rule_version_replaces_the_card(self) -> None:
        asked = _at(2026, 11, 8, 17, 30)
        conversation_id = self._talk(asked)
        _set_rule_version(self.db.path, conversation_id, "5.0-old")

        def summarize(
            utterances: tuple[str, ...],
            card: SessionCard,
        ) -> IdleBrief | None:
            del utterances, card
            return IdleBrief((CardEntry(_NEW_ENTRY, None, _NEW_TITLE),), "5.1", 1, 1)

        scan_idle_summaries(self.db, summarize, asked + IDLE_SUMMARY_SECONDS)
        self.assertEqual(load_card(self.db, conversation_id).rule_version, "5.1")

    def test_model_failure_keeps_card_and_can_retry(self) -> None:
        asked = _at(2026, 11, 8, 18, 0)
        conversation_id = self._talk(asked)
        calls: list[int] = []

        def summarize(
            utterances: tuple[str, ...],
            card: SessionCard,
        ) -> IdleBrief | None:
            del utterances, card
            calls.append(1)
            if len(calls) == 1:
                self.assertIsNone(parse_idle_summary(_CONCLUSION))
                raise RuntimeError("模型没有返回条目列表")
            return _brief()

        moment = asked + IDLE_SUMMARY_SECONDS
        first = scan_idle_summaries(self.db, summarize, moment)
        self.assertEqual(first, 0)
        self.assertEqual(load_card(self.db, conversation_id).entries[0].entry_id, _ENTRY)
        self.assertEqual(_folded_users(self.db.path, conversation_id), [0])
        self.assertEqual(_spend(self.db.path), 0)
        self.assertEqual(open_uses(self.db, self.account.user_id), 0)
        second = scan_idle_summaries(self.db, summarize, moment)
        self.assertEqual(second, 1)
        self.assertEqual(load_card(self.db, conversation_id).entries[0].entry_id, _NEW_ENTRY)
        self.assertEqual(_folded_users(self.db.path, conversation_id), [1])

    def test_prose_from_the_model_is_a_failure(self) -> None:
        self.assertIsNone(parse_idle_summary(_CONCLUSION))
        self.assertIsNone(parse_idle_summary('{"conclusion": "8d6"}'))
        parsed = parse_idle_summary(json.dumps({
            "rule_version": "5.1",
            "entries": [{
                "entry_id": _NEW_ENTRY,
                "concept_key": _NEW_CONCEPT,
                "title": _NEW_TITLE,
            }],
        }, ensure_ascii=False))
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.entries[0].title, _NEW_TITLE)
        self.assertEqual(parsed.rule_version, "5.1")
        listed = parse_idle_summary(json.dumps([
            {"entry_id": _NEW_ENTRY, "title": _NEW_TITLE},
        ]))
        self.assertIsNotNone(listed)
        assert listed is not None
        self.assertEqual(listed.entries[0].entry_id, _NEW_ENTRY)
        self.assertIsNone(listed.rule_version)
        self.assertIsNone(parse_idle_summary('[{"title": "只有标题"}]'))
        asked = _at(2026, 11, 8, 18, 30)
        conversation_id = self._talk(asked)

        def summarize(
            utterances: tuple[str, ...],
            card: SessionCard,
        ) -> IdleBrief | None:
            del utterances, card
            return parse_idle_summary(_CONCLUSION)

        scan_idle_summaries(self.db, summarize, asked + IDLE_SUMMARY_SECONDS)
        self.assertEqual(load_card(self.db, conversation_id).entries[0].entry_id, _ENTRY)
        self.assertEqual(_folded_users(self.db.path, conversation_id), [0])
        self.assertEqual(_spend(self.db.path), 0)

    def test_newer_question_during_scan_is_not_overwritten(self) -> None:
        asked = _at(2026, 11, 8, 19, 0)
        conversation_id = self._talk(asked)

        def summarize(
            utterances: tuple[str, ...],
            card: SessionCard,
        ) -> IdleBrief | None:
            del utterances, card
            commit_turn(
                self.db,
                self.account.user_id,
                conversation_id,
                _FOLLOW,
                "新的助手回答",
                None,
                asked + 1,
            )
            return _brief()

        scan_idle_summaries(self.db, summarize, asked + IDLE_SUMMARY_SECONDS)
        stored = load_card(self.db, conversation_id)
        self.assertEqual(stored.entries[0].entry_id, _ENTRY)
        self.assertNotEqual(stored.entries[0].entry_id, _NEW_ENTRY)
        self.assertEqual(_folded_users(self.db.path, conversation_id), [0, 0])
        self.assertIn(_FOLLOW, _user_bodies(self.db.path, conversation_id))

    def test_site_cap_does_not_call_or_fold(self) -> None:
        asked = _at(2026, 11, 8, 20, 0)
        conversation_id = self._talk(asked)
        moment = asked + IDLE_SUMMARY_SECONDS
        reserve_quota(
            self.db,
            self.account.user_id,
            self.account.created_at,
            yuan_to_micro(SITE_DAILY_CAP_YUAN),
            moment,
        )
        calls: list[int] = []

        def summarize(
            utterances: tuple[str, ...],
            card: SessionCard,
        ) -> IdleBrief | None:
            del utterances, card
            calls.append(1)
            return _brief()

        before = _spend(self.db.path)
        self.assertGreaterEqual(before, SITE_DAILY_CAP_MICRO)
        found = scan_idle_summaries(self.db, summarize, moment)
        self.assertEqual(found, 0)
        self.assertEqual(calls, [])
        self.assertEqual(_folded_users(self.db.path, conversation_id), [0])
        self.assertEqual(load_card(self.db, conversation_id).entries[0].entry_id, _ENTRY)
        self.assertEqual(_spend(self.db.path), before)

    def test_alert_once_when_idle_cost_crosses_the_line(self) -> None:
        asked = _at(2026, 11, 8, 21, 0)
        self._talk(asked)
        moment = asked + IDLE_SUMMARY_SECONDS
        micro = round(qwen_yuan(_ALERT_PROMPT, _ALERT_COMPLETION) * MICRO_PER_YUAN)
        self.assertGreaterEqual(micro, SITE_DAILY_ALERT_MICRO)
        self.assertLess(micro, SITE_DAILY_CAP_MICRO)
        sent: list[int] = []

        def summarize(
            utterances: tuple[str, ...],
            card: SessionCard,
        ) -> IdleBrief | None:
            del utterances, card
            return IdleBrief(
                (CardEntry(_NEW_ENTRY, _NEW_CONCEPT, _NEW_TITLE),),
                "5.1",
                _ALERT_PROMPT,
                _ALERT_COMPLETION,
            )

        scan_idle_summaries(self.db, summarize, moment, on_alert=lambda: sent.append(1))
        self.assertEqual(sent, [1])
        self._talk(moment)
        scan_idle_summaries(
            self.db,
            _always,
            moment + IDLE_SUMMARY_SECONDS,
            on_alert=lambda: sent.append(1),
        )
        self.assertEqual(sent, [1])

    def test_alert_failure_keeps_the_folded_card(self) -> None:
        asked = _at(2026, 11, 8, 21, 30)
        conversation_id = self._talk(asked)

        def boom() -> None:
            raise RuntimeError("smtp down")

        def summarize(
            utterances: tuple[str, ...],
            card: SessionCard,
        ) -> IdleBrief | None:
            del utterances, card
            return IdleBrief(
                (CardEntry(_NEW_ENTRY, _NEW_CONCEPT, _NEW_TITLE),),
                "5.1",
                _ALERT_PROMPT,
                _ALERT_COMPLETION,
            )

        scan_idle_summaries(
            self.db,
            summarize,
            asked + IDLE_SUMMARY_SECONDS,
            on_alert=boom,
        )
        stored = load_card(self.db, conversation_id)
        self.assertEqual(stored.entries[0].entry_id, _NEW_ENTRY)
        self.assertEqual(_folded_users(self.db.path, conversation_id), [1])
        self.assertGreaterEqual(_spend(self.db.path), SITE_DAILY_ALERT_MICRO)

    def test_existing_message_table_gains_folded_column(self) -> None:
        path = self.db.path
        _create_legacy_messages(path)
        list_conversations(self.db, self.account.user_id)
        self.assertIn("folded", _columns(path, "conversation_messages"))
        self.assertEqual(_legacy_folded(path), 0)

    def test_existing_quota_rows_gain_ask_kind(self) -> None:
        path = Path(self._dir.name) / "legacy.sqlite"
        moment = _at(2026, 11, 8, 8, 0)
        _create_legacy_quota(path, "u_existing", moment)
        legacy = AuthDb(path)
        self.assertIn("use_kind", _columns(path, "quota_uses"))
        self.assertEqual(_legacy_kind(path), USE_KIND_ASK)
        self.assertEqual(open_uses(legacy, "u_existing"), 1)

    def test_fake_user_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            record_idle_cost(self.db, _FAKE_USER, 0, _at(2026, 11, 8, 12, 0))

    def test_background_scan_does_not_block_or_sleep(self) -> None:
        asked = _at(2026, 11, 8, 12, 30)
        self._talk(asked)
        seen: list[tuple[str, ...]] = []

        def summarize(
            utterances: tuple[str, ...],
            card: SessionCard,
        ) -> IdleBrief | None:
            del card
            seen.append(utterances)
            return _brief()

        started = time.monotonic()
        handle = start_idle_scan(
            self.db,
            summarize,
            clock=lambda: asked + IDLE_SUMMARY_SECONDS,
            on_alert=None,
        )
        self.assertLess(time.monotonic() - started, 1)
        self.assertTrue(handle.thread.daemon)
        self.assertTrue(handle.thread.is_alive())
        deadline = time.monotonic() + _JOIN_SECONDS
        while not seen and time.monotonic() < deadline:
            time.sleep(0.01)
        handle.stop.set()
        handle.thread.join(_JOIN_SECONDS)
        self.assertFalse(handle.thread.is_alive())
        self.assertEqual(seen, [(_QUESTION,)])
        self.assertLess(time.monotonic() - started, IDLE_SUMMARY_SECONDS)

    def test_qwen_summary_parses_entries_without_network(self) -> None:
        payload = json.dumps({
            "rule_version": "5.1",
            "entries": [{
                "entry_id": _NEW_ENTRY,
                "concept_key": _NEW_CONCEPT,
                "title": _NEW_TITLE,
            }],
        }, ensure_ascii=False)
        client = _FakeClient(payload, _PROMPT_TOKENS, _COMPLETION_TOKENS)
        brief = summarize_with_qwen((_QUESTION,), _card(), client)
        self.assertIsNotNone(brief)
        assert brief is not None
        self.assertEqual(brief.entries[0].entry_id, _NEW_ENTRY)
        self.assertEqual(brief.prompt_tokens, _PROMPT_TOKENS)
        self.assertEqual(brief.completion_tokens, _COMPLETION_TOKENS)
        system = str(client.messages[0]["content"])
        user = str(client.messages[1]["content"])
        self.assertIn("不写规则结论", system)
        self.assertIn(_QUESTION, user)
        self.assertIn(_ENTRY, user)
        self.assertNotIn(_ASSISTANT, user)
        prose = summarize_with_qwen((_QUESTION,), _card(), _FakeClient(_CONCLUSION, 3, 4))
        self.assertIsNone(prose)

    def _talk(self, asked: int, account: Account | None = None) -> str:
        owner = self.account if account is None else account
        return commit_turn(
            self.db,
            owner.user_id,
            None,
            _QUESTION,
            _ASSISTANT,
            (CardEntry(_ENTRY, _CONCEPT, _TITLE),),
            asked,
        )

    def _user(self, email: str, created_at: int) -> Account:
        salt = ip_salt()
        assert salt is not None
        account = self.db.login_email(email, hash_ip(TEST_IP, salt), created_at)
        self.assertIsInstance(account, Account)
        assert isinstance(account, Account)
        return account


def _always(utterances: tuple[str, ...], card: SessionCard) -> IdleBrief | None:
    del utterances, card
    return _brief()


def _brief() -> IdleBrief:
    return IdleBrief(
        (CardEntry(_NEW_ENTRY, _NEW_CONCEPT, _NEW_TITLE),),
        "5.1",
        _PROMPT_TOKENS,
        _COMPLETION_TOKENS,
    )


def _card() -> SessionCard:
    return SessionCard(RULE_VERSION, (CardEntry(_ENTRY, _CONCEPT, _TITLE),))


def _at(year: int, month: int, day: int, hour: int, minute: int) -> int:
    local = datetime(year, month, day, hour, minute, tzinfo=_ZONE)
    return int(local.timestamp())


def _count_allowed(
    db: AuthDb,
    account: Account,
    start: int,
    attempts: int,
    gap: int,
) -> int:
    allowed = 0
    for index in range(attempts):
        result = reserve_quota(
            db,
            account.user_id,
            account.created_at,
            0,
            start + index * gap,
        )
        if isinstance(result, Reserved):
            allowed += 1
    return allowed


def _spend(path: Path) -> int:
    row = _one(path, "SELECT COALESCE(SUM(cost_micro), 0) FROM quota_uses WHERE released = 0")
    return int(row[0])


def _idle_charge(path: Path) -> tuple[str, str]:
    row = _one(
        path,
        "SELECT use_kind, user_id FROM quota_uses WHERE use_kind = ?",
        (USE_KIND_IDLE,),
    )
    return str(row[0]), str(row[1])


def _card_json(path: Path, conversation_id: str) -> str:
    row = _one(
        path,
        "SELECT entries_json FROM session_cards WHERE conversation_id = ?",
        (conversation_id,),
    )
    return str(row[0])


def _folded_users(path: Path, conversation_id: str) -> list[int]:
    return _folded(path, conversation_id, "user")


def _folded_assistants(path: Path, conversation_id: str) -> list[int]:
    return _folded(path, conversation_id, "assistant")


def _folded(path: Path, conversation_id: str, role: str) -> list[int]:
    rows = _all(
        path,
        """
        SELECT folded FROM conversation_messages
        WHERE conversation_id = ? AND role = ?
        ORDER BY position ASC
        """,
        (conversation_id, role),
    )
    return [int(row[0]) for row in rows]


def _user_bodies(path: Path, conversation_id: str) -> list[str]:
    rows = _all(
        path,
        """
        SELECT body FROM conversation_messages
        WHERE conversation_id = ? AND role = ?
        ORDER BY position ASC
        """,
        (conversation_id, "user"),
    )
    return [str(row[0]) for row in rows]


def _set_rule_version(path: Path, conversation_id: str, version: str) -> None:
    _write(
        path,
        "UPDATE session_cards SET rule_version = ? WHERE conversation_id = ?",
        (version, conversation_id),
    )


def _insert_message(
    path: Path,
    conversation_id: str,
    role: str,
    body: str,
    created_at: int,
) -> None:
    _write(
        path,
        """
        INSERT INTO conversation_messages (
            conversation_id, position, role, body, created_at, folded
        ) VALUES (?, ?, ?, ?, ?, 0)
        """,
        (conversation_id, 2, role, body, created_at),
    )


def _create_legacy_messages(path: Path) -> None:
    _script(
        path,
        """
        CREATE TABLE conversation_messages (
            message_id INTEGER PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            role TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        INSERT INTO conversation_messages (
            conversation_id, position, role, body, created_at
        ) VALUES ('c_old', 0, 'user', '旧问题', 1);
        """,
    )


def _legacy_folded(path: Path) -> int:
    row = _one(path, "SELECT folded FROM conversation_messages WHERE conversation_id = 'c_old'")
    return int(row[0])


def _create_legacy_quota(path: Path, user_id: str, asked_at: int) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE quota_uses (
                id INTEGER PRIMARY KEY,
                user_id TEXT NOT NULL,
                asked_at INTEGER NOT NULL,
                cost_micro INTEGER NOT NULL,
                released INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO quota_uses (user_id, asked_at, cost_micro, released)
            VALUES (?, ?, ?, 0)
            """,
            (user_id, asked_at, 10),
        )
        connection.commit()
    finally:
        connection.close()


def _legacy_kind(path: Path) -> str:
    row = _one(path, "SELECT use_kind FROM quota_uses")
    return str(row[0])


def _columns(path: Path, table: str) -> set[str]:
    rows = _all(path, f"PRAGMA table_info({table})")
    return {str(row[1]) for row in rows}


def _one(path: Path, sql: str, params: tuple[object, ...] = ()) -> sqlite3.Row:
    rows = _all(path, sql, params)
    if len(rows) != 1:
        raise AssertionError("期望一行")
    return rows[0]


def _all(path: Path, sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        return list(connection.execute(sql, params).fetchall())
    finally:
        connection.close()


def _write(path: Path, sql: str, params: tuple[object, ...]) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(sql, params)
        connection.commit()
    finally:
        connection.close()


def _script(path: Path, sql: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(sql)
        connection.commit()
    finally:
        connection.close()


class _Usage:
    def __init__(self, prompt: int, completion: int) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion

    def model_dump(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Message(content)


class _Response:
    def __init__(self, content: str, prompt: int, completion: int) -> None:
        self.choices = [_Choice(content)]
        self.usage = _Usage(prompt, completion)


class _Completions:
    def __init__(self, response: _Response) -> None:
        self._response = response
        self.messages: list[dict[str, str]] = []

    def create(self, **kwargs: object) -> _Response:
        found = kwargs.get("messages")
        if not isinstance(found, list):
            raise AssertionError("没有消息")
        self.messages = found
        return self._response


class _Chat:
    def __init__(self, response: _Response) -> None:
        self.completions = _Completions(response)


class _FakeClient:
    def __init__(self, content: str, prompt: int, completion: int) -> None:
        self.chat = _Chat(_Response(content, prompt, completion))

    @property
    def messages(self) -> list[dict[str, str]]:
        return self.chat.completions.messages


if __name__ == "__main__":
    unittest.main()
