"""登记失败时撤掉向量。假存储，不连接 PostgreSQL，也不调用嵌入接口。"""

import os
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
from unittest import mock
from unittest.mock import Mock

from admin_store import (
    AdminRejected,
    publish_index,
    published_index_version,
    rebuild_index,
)
from auth_app import AuthApp
from auth_constants import HTTP_UNPROCESSABLE, PATH_ADMIN_INDEX_REBUILD
from auth_db import Account, AuthDb
from auth_identity import hash_ip, ip_salt
from auth_routes import route_api
from auth_session import issue_for, now_seconds
from auth_testkit import TEST_IP, auth_env, cookie_header, json_bytes, make_request
from demo_server import open_published_demo
from index_build import rebuild_registered, taken_index_versions
from modules.vector_rows import ChunkVector
from modules.vector_store import DATABASE_URL_ENV, PgChunkIndex

ACTOR = "u_indexbuilder"
WHEN = datetime(2026, 11, 10, 2, 47, tzinfo=timezone.utc)
KEPT = "idx-kept-pointer"
COLLISION = "idx-collision-01"
ADMIN_EMAIL = "admin@example.com"
CSRF = "csrf-token-for-tests-0001"
FAKE_DATABASE_URL = "postgresql://db.example/elderbrain"


class FakeStore:
    """记下替换和删除。不连接数据库。"""

    def __init__(self) -> None:
        self.replaced: list[tuple[str, tuple[ChunkVector, ...]]] = []
        self.deleted: list[str] = []

    def replace_version(
        self,
        index_version: str,
        chunks: Sequence[ChunkVector],
    ) -> None:
        self.replaced.append((index_version, tuple(chunks)))

    def delete_version(self, index_version: str) -> None:
        self.deleted.append(index_version)


class IndexRegisterTests(unittest.TestCase):
    def test_sqlite_failure_deletes_version_and_keeps_pointer(self) -> None:
        db = _db(self)
        _publish_kept(db)
        rebuild_index(db, ACTOR, lambda: COLLISION, WHEN)
        store = FakeStore()

        def builder() -> str:
            store.replace_version(COLLISION, ())
            return COLLISION

        with self.assertRaises(AdminRejected):
            rebuild_registered(db, ACTOR, builder, store.delete_version, WHEN)
        self.assertEqual(store.deleted, [COLLISION])
        self.assertEqual(published_index_version(db), KEPT)
        self.assertEqual(taken_index_versions(db), {KEPT, COLLISION})

    def test_route_drops_vectors_when_version_exists(self) -> None:
        holder = auth_env({"ADMIN_IDENTITIES": f"email:{ADMIN_EMAIL}"})
        holder.__enter__()
        self.addCleanup(lambda: holder.__exit__(None, None, None))
        app = AuthApp(_db(self), lambda _recipient, _code: None)
        _publish_kept(app.db)
        rebuild_index(app.db, ACTOR, lambda: COLLISION, WHEN)
        store = FakeStore()

        def builder() -> str:
            store.replace_version(COLLISION, ())
            return COLLISION

        app.index_builder = builder
        app.drop_index_version = store.delete_version
        _account, headers = _admin_headers(app)
        response = route_api(
            app,
            make_request("POST", PATH_ADMIN_INDEX_REBUILD, json_bytes({}), headers),
            lambda _question, _card: "规则回答",
            None,
        )
        self.assertEqual(response.status, HTTP_UNPROCESSABLE)
        self.assertEqual(store.deleted, [COLLISION])
        self.assertEqual(published_index_version(app.db), KEPT)

    def test_demo_startup_installs_builder_without_writing(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        auth = AuthApp(
            AuthDb(Path(folder.name) / "auth.sqlite"),
            lambda _recipient, _code: None,
        )
        with _startup_patches(auth) as replace:
            _opened, opened_auth = open_published_demo()
        replace.assert_not_called()
        self.assertTrue(callable(opened_auth.index_builder))
        self.assertTrue(callable(opened_auth.drop_index_version))


@contextmanager
def _startup_patches(auth: AuthApp) -> Iterator[Mock]:
    """叠上启动替身。退出时恢复，并交出 replace_version 的替身。"""
    with ExitStack() as stack:
        stack.enter_context(
            mock.patch.dict(
                os.environ,
                {DATABASE_URL_ENV: FAKE_DATABASE_URL},
                clear=False,
            ),
        )
        stack.enter_context(
            mock.patch("demo_server._selected_rag", return_value=mock.Mock()),
        )
        stack.enter_context(mock.patch("demo_server.open_auth", return_value=auth))
        stack.enter_context(mock.patch.object(PgChunkIndex, "ensure_schema"))
        replace = stack.enter_context(
            mock.patch.object(PgChunkIndex, "replace_version"),
        )
        stack.enter_context(
            mock.patch(
                "modules.api_embeddings.load_embedding3",
                side_effect=AssertionError("startup embed"),
            ),
        )
        yield replace


def _db(case: unittest.TestCase) -> AuthDb:
    folder = tempfile.TemporaryDirectory()
    case.addCleanup(folder.cleanup)
    return AuthDb(Path(folder.name) / "auth.sqlite")


def _publish_kept(db: AuthDb) -> None:
    rebuild_index(db, ACTOR, lambda: KEPT, WHEN)
    publish_index(db, ACTOR, KEPT, WHEN)


def _admin_headers(app: AuthApp) -> tuple[Account, dict[str, str]]:
    moment = now_seconds()
    salt = ip_salt()
    assert salt is not None
    account = app.db.login_email(ADMIN_EMAIL, hash_ip(TEST_IP, salt), moment)
    assert isinstance(account, Account)
    issued = issue_for(account, moment)
    assert issued is not None
    token, _role = issued
    headers = {"cookie": cookie_header(token, CSRF), "x-csrf-token": CSRF}
    return account, headers


if __name__ == "__main__":
    unittest.main()
