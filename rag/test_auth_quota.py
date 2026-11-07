"""每日、每分钟和全站配额。"""

import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from auth_constants import (
    DAILY_LIMIT,
    MINUTE_LIMIT,
    MINUTE_WINDOW_SECONDS,
    NEW_ACCOUNT_DAILY_LIMIT,
    NEW_ACCOUNT_WINDOW_SECONDS,
    QUOTA_DAILY,
    QUOTA_MINUTE,
    QUOTA_SITE,
)
from auth_db import Account, AuthDb
from auth_identity import hash_ip, ip_salt
from auth_quota import (
    QuotaDeny,
    Reserved,
    open_uses,
    quota_message,
    release_quota,
    reserve_quota,
    yuan_to_micro,
)
from auth_testkit import TEST_IP, auth_env

_ZONE = ZoneInfo("Asia/Shanghai")
# 隔开滑动窗口，请求才会落到每日上限而不是每分钟上限。
_GAP = MINUTE_WINDOW_SECONDS + 1


class QuotaTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = auth_env()
        self._env.__enter__()
        self._dir = tempfile.TemporaryDirectory()
        self.db = AuthDb(Path(self._dir.name) / "auth.sqlite")

    def tearDown(self) -> None:
        self._dir.cleanup()
        self._env.__exit__(None, None, None)

    def test_new_account_stops_at_twenty(self) -> None:
        created = _at(2026, 11, 7, 12, 0)
        account = self._user("new@example.com", created)
        self._fill(account, created, NEW_ACCOUNT_DAILY_LIMIT)
        denied = self._once(account, created + NEW_ACCOUNT_DAILY_LIMIT * _GAP)
        self.assertEqual(denied.kind, QUOTA_DAILY)
        message = quota_message(denied.kind, denied.resume_at)
        self.assertIn("每日次数", message)
        self.assertIn("2026-11-08 00:00:00", message)

    def test_daily_cap_is_one_hundred_after_the_first_day(self) -> None:
        created = _at(2026, 11, 1, 8, 0)
        start = _at(2026, 11, 7, 8, 0)
        account = self._user("old@example.com", created)
        self.assertGreaterEqual(start - created, NEW_ACCOUNT_WINDOW_SECONDS)
        self._fill(account, start, DAILY_LIMIT)
        denied = self._once(account, start + DAILY_LIMIT * _GAP)
        self.assertEqual(denied.kind, QUOTA_DAILY)
        self.assertIn("2026-11-08 00:00:00", quota_message(denied.kind, denied.resume_at))

    def test_first_window_can_open_before_midnight(self) -> None:
        created = _at(2026, 11, 7, 10, 0)
        start = _at(2026, 11, 8, 8, 0)
        account = self._user("morning@example.com", created)
        self._fill(account, start, NEW_ACCOUNT_DAILY_LIMIT)
        denied = self._once(account, start + NEW_ACCOUNT_DAILY_LIMIT * _GAP)
        message = quota_message(denied.kind, denied.resume_at)
        self.assertIn("每日次数", message)
        self.assertIn("2026-11-08 10:00:00", message)

    def test_minute_window_stops_at_ten(self) -> None:
        created = _at(2026, 11, 1, 8, 0)
        now = _at(2026, 11, 7, 15, 0)
        account = self._user("fast@example.com", created)
        for _index in range(MINUTE_LIMIT):
            self.assertIsInstance(self._once(account, now), Reserved)
        denied = self._once(account, now)
        self.assertIsInstance(denied, QuotaDeny)
        assert isinstance(denied, QuotaDeny)
        self.assertEqual(denied.kind, QUOTA_MINUTE)
        self.assertEqual(denied.resume_at, now + MINUTE_WINDOW_SECONDS)
        message = quota_message(denied.kind, denied.resume_at)
        self.assertIn("每分钟次数", message)
        self.assertIn("恢复时间", message)
        self.assertIsInstance(self._once(account, denied.resume_at), Reserved)

    def test_site_cap_uses_recorded_cost(self) -> None:
        created = _at(2026, 11, 1, 8, 0)
        now = _at(2026, 11, 7, 16, 0)
        account = self._user("cost@example.com", created)
        first = reserve_quota(self.db, account.user_id, created, yuan_to_micro(21), now)
        self.assertIsInstance(first, Reserved)
        denied = reserve_quota(self.db, account.user_id, created, 0, now + _GAP)
        self.assertIsInstance(denied, QuotaDeny)
        assert isinstance(denied, QuotaDeny)
        self.assertEqual(denied.kind, QUOTA_SITE)
        message = quota_message(denied.kind, denied.resume_at)
        self.assertIn("全站上限", message)
        self.assertIn("2026-11-08 00:00:00", message)

    def test_parallel_reserves_stop_at_the_minute_cap(self) -> None:
        created = _at(2026, 11, 1, 8, 0)
        now = _at(2026, 11, 7, 17, 0)
        account = self._user("race@example.com", created)
        results: list[Reserved | QuotaDeny] = []
        barrier = threading.Barrier(MINUTE_LIMIT + 5)
        lock = threading.Lock()

        def worker() -> None:
            barrier.wait()
            result = reserve_quota(self.db, account.user_id, created, 0, now)
            with lock:
                results.append(result)

        threads = [threading.Thread(target=worker) for _index in range(MINUTE_LIMIT + 5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        allowed = [item for item in results if isinstance(item, Reserved)]
        self.assertEqual(len(allowed), MINUTE_LIMIT)

    def test_release_removes_the_spend(self) -> None:
        created = _at(2026, 11, 1, 8, 0)
        now = _at(2026, 11, 7, 18, 0)
        account = self._user("fail@example.com", created)
        reserved = self._once(account, now)
        assert isinstance(reserved, Reserved)
        release_quota(self.db, reserved.event_id)
        self.assertEqual(open_uses(self.db, account.user_id), 0)
        self.assertIsInstance(self._once(account, now), Reserved)

    def _user(self, email: str, created_at: int) -> Account:
        salt = ip_salt()
        assert salt is not None
        account = self.db.login_email(email, hash_ip(TEST_IP, salt), created_at)
        self.assertIsInstance(account, Account)
        assert isinstance(account, Account)
        return account

    def _fill(self, account: Account, start: int, count: int) -> None:
        for index in range(count):
            result = self._once(account, start + index * _GAP)
            self.assertIsInstance(result, Reserved)

    def _once(self, account: Account, now: int) -> Reserved | QuotaDeny:
        return reserve_quota(self.db, account.user_id, account.created_at, 0, now)


def _at(year: int, month: int, day: int, hour: int, minute: int) -> int:
    local = datetime(year, month, day, hour, minute, tzinfo=_ZONE)
    return int(local.timestamp())


if __name__ == "__main__":
    unittest.main()
