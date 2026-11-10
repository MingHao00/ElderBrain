"""已发布版本的 pgvector 检索。不连接 PostgreSQL，也不调用嵌入接口。"""

import math
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
from unittest import mock

from admin_store import (
    publish_index,
    published_index_version,
    rebuild_index,
    set_source_enabled,
)
from auth_db import AuthDb
from config import RULE_VERSION, SOURCE_ID
from modules.api_embeddings import EMBEDDING3_DIMENSIONS
from modules.data_preparation import RagPiece
from demo_server import main, open_published_demo
from modules.index_construction import IndexConstructionModule
from modules.published_retrieval import PublishedHybridSearch, enabled_source_ids
from modules.retrieval import bm25_hits
from modules.vector_store import DATABASE_URL_ENV, ChunkVector, PgChunkIndex
from system import ElderBrainRag

PUBLISHED = "idx-2026-11-09-01"
BUILDING = "idx-2026-11-09-02"
ACTOR = "u_vectorwriter"
WHEN = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
QUERY = "火球术的射程是多少"
RELEVANT = "火球术射程一百五十尺"
HIDDEN = "构建中火球术射程一百五十尺"
DISABLED_SOURCE = "src-other-off"
SEARCH_PARAMS = 5
VISIBLE_PARAMS = 3
INSERT_PARAMS = 7
DELETE_PARAMS = 1


class PublishedFilterTests(unittest.TestCase):
    def test_search_uses_only_the_published_version(self) -> None:
        conn = FakeConn()
        conn.committed = [
            _stored(PUBLISHED, "已发布", chunk_id="pub"),
            _stored(BUILDING, HIDDEN, chunk_id="draft"),
            _stored(PUBLISHED, "规则不符", chunk_id="rule", rule_version="5.2"),
            _stored(
                PUBLISHED,
                "来源已停",
                chunk_id="off",
                source_id=DISABLED_SOURCE,
            ),
        ]
        pointer = {"value": PUBLISHED}
        store = PgChunkIndex(lambda: conn, lambda: pointer["value"])
        found = store.search(RULE_VERSION, (SOURCE_ID,), _vector(1.0), 5)
        self.assertEqual([piece.text for piece in found], ["已发布"])
        self.assertEqual(found[0].metadata["entry_id"], "srd51:spell:fireball")
        self.assertEqual(_select_params(conn)[0], PUBLISHED)
        pointer["value"] = None
        conn.statements.clear()
        self.assertEqual(
            store.search(RULE_VERSION, (SOURCE_ID,), _vector(1.0), 5),
            [],
        )
        self.assertEqual(conn.statements, [])

    def test_search_orders_by_cosine_distance(self) -> None:
        conn = FakeConn()
        conn.committed = [
            _stored(PUBLISHED, "远", chunk_id="far", embedding=_vector(0.0, 1.0)),
            _stored(PUBLISHED, "近", chunk_id="near", embedding=_vector(1.0, 0.0)),
        ]
        store = PgChunkIndex(lambda: conn, lambda: PUBLISHED)
        found = store.search(RULE_VERSION, (SOURCE_ID,), _vector(1.0, 0.0), 1)
        self.assertEqual([piece.text for piece in found], ["近"])
        sql = _select_sql(conn)
        self.assertIn("<=>", sql)
        self.assertNotIn("<->", sql)

    def test_short_vector_does_not_connect(self) -> None:
        def connect() -> FakeConn:
            raise AssertionError("不应连接")

        store = PgChunkIndex(connect, lambda: PUBLISHED)
        with self.assertRaises(ValueError):
            store.search(RULE_VERSION, (SOURCE_ID,), (0.1, 0.2), 1)

    def test_schema_uses_embedding_dimensions(self) -> None:
        conn = FakeConn()
        store = PgChunkIndex(lambda: conn, lambda: None)
        store.ensure_schema()
        joined = "\n".join(sql for sql, _params in conn.statements)
        self.assertIn("CREATE EXTENSION IF NOT EXISTS vector", joined)
        self.assertIn(f"vector({EMBEDDING3_DIMENSIONS})", joined)
        self.assertEqual(conn.transaction_count, 1)
        source = Path(__file__).parent.joinpath("modules", "vector_store.py")
        self.assertNotIn("2048", source.read_text(encoding="utf-8"))


class VersionWriteTests(unittest.TestCase):
    def test_replace_version_does_not_move_pointer(self) -> None:
        folder, db = _published_db(PUBLISHED)
        conn = FakeConn()
        try:
            store = PgChunkIndex(lambda: conn, lambda: published_index_version(db))
            store.replace_version(BUILDING, [_chunk("新版本正文")])
            self.assertEqual(published_index_version(db), PUBLISHED)
            for sql, params in conn.statements:
                self.assertNotIn("published_pointer", sql)
                self.assertNotIn(BUILDING, sql)
                self.assertTrue(_contains(params, BUILDING))
        finally:
            folder.cleanup()

    def test_user_text_stays_in_parameters(self) -> None:
        conn = FakeConn()
        nasty = "火球'); DROP TABLE chunk_vectors;--"
        store = PgChunkIndex(lambda: conn, lambda: PUBLISHED)
        store.replace_version(BUILDING, [_chunk(nasty)])
        self.assertTrue(any(_contains(params, nasty) for _sql, params in conn.statements))
        for sql, _params in conn.statements:
            self.assertNotIn(nasty, sql)
            self.assertNotIn("DROP TABLE", sql)

    def test_replace_version_is_one_transaction(self) -> None:
        conn = FakeConn()
        conn.committed = [
            _stored(PUBLISHED, "已发布", chunk_id="pub"),
            _stored(BUILDING, "旧正文", chunk_id="old"),
        ]
        store = PgChunkIndex(lambda: conn, lambda: PUBLISHED)
        store.replace_version(BUILDING, [
            _chunk("新甲", chunk_id="new-a"),
            _chunk("新乙", chunk_id="new-b"),
        ])
        bodies = sorted(row["body"] for row in conn.committed)
        self.assertEqual(bodies, ["已发布", "新乙", "新甲"])
        self.assertEqual(conn.transaction_count, 1)
        self.assertEqual(conn.commit_count, 1)
        kinds = [_kind(sql) for sql, _params in conn.statements]
        self.assertEqual(kinds[0], "delete")
        self.assertEqual(kinds.count("insert"), 2)

    def test_replace_failure_rolls_back_the_version(self) -> None:
        conn = FakeConn(fail_on="INSERT")
        conn.committed = [_stored(BUILDING, "旧正文", chunk_id="old")]
        store = PgChunkIndex(lambda: conn, lambda: PUBLISHED)
        with self.assertRaises(RuntimeError):
            store.replace_version(BUILDING, [_chunk("新正文")])
        self.assertEqual(conn.transaction_count, 1)
        self.assertEqual(conn.commit_count, 0)
        self.assertEqual(conn.rollback_count, 1)
        self.assertEqual(conn.committed[0]["body"], "旧正文")

    def test_replace_uses_cursor_without_connection_executemany(self) -> None:
        conn = _CursorConn()
        store = PgChunkIndex(lambda: conn, lambda: None)
        store.replace_version(BUILDING, [_chunk("游标写入", chunk_id="cur-1")])
        self.assertEqual(len(conn.batches), 1)
        sql, rows = conn.batches[0]
        self.assertIn("INSERT", sql)
        self.assertEqual(rows[0][4], "游标写入")
        self.assertNotIn("published_pointer", sql)

    def test_delete_version_is_one_transaction(self) -> None:
        conn = FakeConn()
        conn.committed = [
            _stored(BUILDING, "甲", chunk_id="a"),
            _stored(BUILDING, "乙", chunk_id="b"),
            _stored(PUBLISHED, "留", chunk_id="keep"),
        ]
        store = PgChunkIndex(lambda: conn, lambda: PUBLISHED)
        store.delete_version(BUILDING)
        self.assertEqual([row["body"] for row in conn.committed], ["留"])
        deletes = [sql for sql, _params in conn.statements if _kind(sql) == "delete"]
        self.assertEqual(len(deletes), 1)
        self.assertEqual(conn.transaction_count, 1)
        self.assertEqual(conn.commit_count, 1)

    def test_requirements_pin_psycopg_major(self) -> None:
        text = Path(__file__).with_name("requirements.txt").read_text(encoding="utf-8")
        self.assertIn("faiss-cpu", text)
        self.assertRegex(text, r"psycopg\[binary\]>=3\.\d+.*<4")


class HybridPublishedTests(unittest.TestCase):
    def test_bm25_does_not_see_unpublished_text(self) -> None:
        visible = [
            _piece(RELEVANT, "fire"),
            _piece("猫头鹰", "owl"),
            _piece("巨龙", "dragon"),
        ]
        store = _SplitStore(visible, HIDDEN)
        embed = _RecordingEmbed()
        hybrid = PublishedHybridSearch(store, embed.embed_query, lambda: (SOURCE_ID,))
        found = [piece.text for piece in hybrid.hybrid_search(QUERY)]
        self.assertEqual(found, [RELEVANT])
        self.assertNotIn(HIDDEN, found)
        self.assertEqual(store.search_filters, [(RULE_VERSION, (SOURCE_ID,))])
        self.assertEqual(embed.calls, 1)

    def test_no_visible_chunks_skips_embedding(self) -> None:
        store = _SplitStore([_piece(RELEVANT, "fire")], HIDDEN)
        hybrid = PublishedHybridSearch(store, _forbid_embed, lambda: ())
        self.assertEqual(hybrid.hybrid_search(QUERY), [])
        self.assertEqual(store.searches, 0)
        self.assertEqual(store.seen_enabled, ())

    def test_bm25_hits_only_score_the_given_list(self) -> None:
        allowed = [
            _piece(RELEVANT, "fire"),
            _piece("猫头鹰", "owl"),
            _piece("巨龙", "dragon"),
        ]
        found = [piece.text for piece in bm25_hits(allowed, QUERY)]
        self.assertEqual(found, [RELEVANT])

    def test_disabled_source_drops_out_of_the_filter(self) -> None:
        folder, db = _published_db(PUBLISHED)
        try:
            self.assertEqual(enabled_source_ids(db), (SOURCE_ID,))
            set_source_enabled(db, ACTOR, SOURCE_ID, False, WHEN)
            self.assertEqual(enabled_source_ids(db), ())
            self.assertEqual(published_index_version(db), PUBLISHED)
        finally:
            folder.cleanup()


class _SplitStore:
    """可见块与未发布正文分开。检索不该读到 hidden。"""

    def __init__(self, visible: list[RagPiece], hidden: str) -> None:
        self.visible = visible
        self.hidden = hidden
        self.searches = 0
        self.search_filters: list[tuple[str, tuple[str, ...]]] = []
        self.seen_enabled: tuple[str, ...] = ()

    def visible_chunks(
        self,
        rule_version: str,
        enabled_source_ids: Sequence[str],
    ) -> list[RagPiece]:
        del rule_version
        self.seen_enabled = tuple(enabled_source_ids)
        if not enabled_source_ids:
            return []
        return list(self.visible)

    def search(
        self,
        rule_version: str,
        enabled_source_ids: Sequence[str],
        query_vector: Sequence[float],
        limit: int,
    ) -> list[RagPiece]:
        del query_vector, limit
        self.searches += 1
        self.search_filters.append((rule_version, tuple(enabled_source_ids)))
        return []


class _RecordingEmbed:
    def __init__(self) -> None:
        self.calls = 0

    def embed_query(self, text: str) -> list[float]:
        self.calls += 1
        self.text = text
        return list(_vector(1.0))


class FakeConn:
    """记录事务，并按 SQL 里的过滤列筛行。列不在 SQL 里就整表返回。"""

    def __init__(self, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.committed: list[dict[str, object]] = []
        self.pending: list[dict[str, object]] = []
        self.statements: list[tuple[str, tuple[object, ...]]] = []
        self.transaction_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self._tx = False
        self._result: list[dict[str, object]] = []

    def __enter__(self) -> "FakeConn":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb

    def transaction(self) -> "_FakeTx":
        return _FakeTx(self)

    def execute(
        self,
        sql: str,
        params: Sequence[object] | None = None,
    ) -> "_FakeCursor":
        if not self._tx:
            raise RuntimeError("不在事务里")
        bound = tuple(params) if params is not None else ()
        _reject_interpolated(sql, bound)
        self.statements.append((sql, bound))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("写入失败")
        self._result = self._apply(sql, bound)
        return _FakeCursor(self._result)

    def executemany(self, sql: str, params_seq: Sequence[Sequence[object]]) -> None:
        for params in params_seq:
            self.execute(sql, params)

    def _apply(self, sql: str, params: tuple[object, ...]) -> list[dict[str, object]]:
        kind = _kind(sql)
        if kind == "delete":
            return self._delete(sql, params)
        if kind == "insert":
            return self._insert(params)
        if kind == "select":
            return self._select(sql, params)
        return []

    def _delete(self, sql: str, params: tuple[object, ...]) -> list[dict[str, object]]:
        if "index_version" not in sql or len(params) != DELETE_PARAMS:
            self.pending = []
            return []
        version = params[0]
        self.pending = [row for row in self.pending if row["index_version"] != version]
        return []

    def _insert(self, params: tuple[object, ...]) -> list[dict[str, object]]:
        if len(params) != INSERT_PARAMS:
            raise AssertionError("插入参数个数不符")
        self.pending.append(_inserted(params))
        return []

    def _select(self, sql: str, params: tuple[object, ...]) -> list[dict[str, object]]:
        if not _filters_in_sql(sql):
            return [dict(row) for row in self.pending]
        if "<=>" in sql:
            return _nearest(self.pending, params)
        if len(params) != VISIBLE_PARAMS:
            raise AssertionError("可见块参数个数不符")
        return _project(_filtered(self.pending, params))


class _CursorConn:
    """模仿 psycopg 3：连接能 execute，批量插入只在游标上。"""

    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple[object, ...]]] = []
        self._cursor = _BatchCursor()

    def __enter__(self) -> "_CursorConn":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb

    def transaction(self) -> "_CursorConn":
        return self

    def execute(
        self,
        sql: str,
        params: Sequence[object] | None = None,
    ) -> object:
        bound = tuple(params) if params is not None else ()
        self.statements.append((sql, bound))
        return _FakeCursor([])

    def cursor(self) -> "_BatchCursor":
        return self._cursor

    @property
    def batches(self) -> list[tuple[str, tuple[tuple[object, ...], ...]]]:
        return self._cursor.batches


class _BatchCursor:
    def __init__(self) -> None:
        self.batches: list[tuple[str, tuple[tuple[object, ...], ...]]] = []

    def executemany(self, sql: str, params_seq: Sequence[Sequence[object]]) -> None:
        rows = tuple(tuple(row) for row in params_seq)
        self.batches.append((sql, rows))


class _FakeTx:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    def __enter__(self) -> "_FakeTx":
        if self.conn._tx:
            raise RuntimeError("事务不能嵌套")
        self.conn._tx = True
        self.conn.transaction_count += 1
        self.conn.pending = [dict(row) for row in self.conn.committed]
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc, tb
        self.conn._tx = False
        if exc_type is None:
            self.conn.committed = self.conn.pending
            self.conn.commit_count += 1
            return
        self.conn.rollback_count += 1


class _FakeCursor:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, object]]:
        return [dict(row) for row in self._rows]


def _forbid_embed(text: str) -> list[float]:
    del text
    raise AssertionError("不应嵌入")


def _published_db(version: str) -> tuple[tempfile.TemporaryDirectory[str], AuthDb]:
    folder = tempfile.TemporaryDirectory()
    db = AuthDb(Path(folder.name) / "auth.sqlite")
    rebuild_index(db, ACTOR, lambda: version, WHEN)
    publish_index(db, ACTOR, version, WHEN)
    return folder, db


def _vector(first: float, second: float = 0.0) -> tuple[float, ...]:
    values = [0.0] * EMBEDDING3_DIMENSIONS
    values[0] = first
    values[1] = second
    return tuple(values)


def _piece(text: str, section_id: str) -> RagPiece:
    return RagPiece(
        text,
        {
            "chunk_id": section_id,
            "section_id": section_id,
            "entry_id": f"srd51:rule:{section_id}",
            "rule_version": RULE_VERSION,
            "source_id": SOURCE_ID,
        },
    )


def _chunk(
    body: str,
    chunk_id: str = "chunk-a",
    embedding: tuple[float, ...] | None = None,
) -> ChunkVector:
    piece = RagPiece(
        body,
        {
            "chunk_id": chunk_id,
            "section_id": "fire",
            "entry_id": "srd51:spell:fireball",
            "rule_version": RULE_VERSION,
            "source_id": SOURCE_ID,
        },
    )
    return ChunkVector(piece, embedding or _vector(1.0))


def _stored(
    index_version: str,
    body: str,
    chunk_id: str,
    rule_version: str = RULE_VERSION,
    source_id: str = SOURCE_ID,
    embedding: tuple[float, ...] | None = None,
) -> dict[str, object]:
    return {
        "index_version": index_version,
        "chunk_id": chunk_id,
        "rule_version": rule_version,
        "source_id": source_id,
        "body": body,
        "metadata": {
            "chunk_id": chunk_id,
            "section_id": "fire",
            "entry_id": "srd51:spell:fireball",
            "rule_version": rule_version,
            "source_id": source_id,
        },
        "embedding": list(embedding or _vector(1.0)),
    }


def _inserted(params: tuple[object, ...]) -> dict[str, object]:
    metadata = params[5]
    if not isinstance(metadata, dict):
        raise AssertionError("元数据必须是字典")
    return {
        "index_version": params[0],
        "chunk_id": params[1],
        "rule_version": params[2],
        "source_id": params[3],
        "body": params[4],
        "metadata": dict(metadata),
        "embedding": _parse_vector(params[6]),
    }


def _nearest(
    rows: list[dict[str, object]],
    params: tuple[object, ...],
) -> list[dict[str, object]]:
    if len(params) != SEARCH_PARAMS:
        raise AssertionError("查询参数个数不符")
    matched = _filtered(rows, params)
    query = _parse_vector(params[3])
    ordered = sorted(matched, key=lambda row: _distance(row["embedding"], query))
    limit = params[4]
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise AssertionError("limit 必须是整数")
    return _project(ordered[:limit])


def _filtered(
    rows: list[dict[str, object]],
    params: tuple[object, ...],
) -> list[dict[str, object]]:
    version, rule_version, enabled = params[0], params[1], params[2]
    if not isinstance(enabled, (list, tuple)):
        raise AssertionError("来源参数必须是列表")
    return [
        row
        for row in rows
        if row["index_version"] == version
        and row["rule_version"] == rule_version
        and row["source_id"] in enabled
    ]


def _project(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {"body": row["body"], "metadata": dict(_as_dict(row["metadata"]))}
        for row in rows
    ]


def _select_params(conn: FakeConn) -> tuple[object, ...]:
    return _select_statement(conn)[1]


def _select_sql(conn: FakeConn) -> str:
    return _select_statement(conn)[0]


def _select_statement(conn: FakeConn) -> tuple[str, tuple[object, ...]]:
    for sql, params in conn.statements:
        if _kind(sql) == "select" and "<=>" in sql:
            return sql, params
    raise AssertionError("没有向量查询")


def _kind(sql: str) -> str:
    head = sql.lstrip().split(" ", 1)[0].upper()
    if head == "DELETE":
        return "delete"
    if head == "INSERT":
        return "insert"
    if head == "SELECT":
        return "select"
    return "other"


def _filters_in_sql(sql: str) -> bool:
    return all(name in sql for name in ("index_version", "rule_version", "source_id"))


def _reject_interpolated(sql: str, params: tuple[object, ...]) -> None:
    for text in _strings(params):
        if text and text in sql:
            raise AssertionError("参数被拼进了 SQL")


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        # 列名会和 SQL 里的字段重复。只检查调用方写进来的值。
        found: list[str] = []
        for item in value.values():
            found.extend(_strings(item))
        return found
    if isinstance(value, (list, tuple)):
        found = []
        for item in value:
            found.extend(_strings(item))
        return found
    return []


def _contains(value: object, text: str) -> bool:
    return text in _strings(value)


def _parse_vector(value: object) -> list[float]:
    if isinstance(value, list):
        return [float(item) for item in value]
    if not isinstance(value, str) or not value.startswith("["):
        raise AssertionError("向量参数格式不符")
    inner = value[1:-1]
    if inner == "":
        return []
    return [float(part) for part in inner.split(",")]


def _distance(stored: object, query: Sequence[float]) -> float:
    left = [float(item) for item in _as_list(stored)]
    dot = sum(a * b for a, b in zip(left, query))
    left_norm = math.sqrt(sum(item * item for item in left))
    right_norm = math.sqrt(sum(item * item for item in query))
    if left_norm == 0 or right_norm == 0:
        return 1.0
    return 1.0 - dot / (left_norm * right_norm)


def _as_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AssertionError("元数据必须是字典")
    return value


def _as_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise AssertionError("向量必须是列表")
    return value


SECTION_FIRE = "srd51:spell:fireball"
SECTION_OWL = "srd51:rule:owl"
OLD_PARENT = "已发布旧版射程一百五十尺"
NEW_PARENT = "已发布新版射程六十尺"
LOCAL_FIRE = "启动时本地清单里的火球术句子"
LOCAL_OWL = "启动时本地清单里的猫头鹰句子"
FIRE_EARLY = "前半射程"
FIRE_LATE = "后半范围"
OWL_BODY = "猫头鹰在夜里飞"
ORPHAN_BODY = "无节号孤立句"
FIRE_CHUNK_EARLY = "srd51:spell:fireball#zh#0"
FIRE_CHUNK_LATE = "srd51:spell:fireball#zh#1"
OWL_CHUNK = "srd51:rule:owl#zh#0"


class PublishedParentTests(unittest.TestCase):
    def test_ask_parent_follows_published_pointer(self) -> None:
        conn = FakeConn()
        conn.committed = [
            _published_row(PUBLISHED, OLD_PARENT, FIRE_CHUNK_EARLY, SECTION_FIRE),
            _published_row(BUILDING, NEW_PARENT, FIRE_CHUNK_EARLY, SECTION_FIRE),
        ]
        pointer = {"value": PUBLISHED}
        inner = PgChunkIndex(lambda: conn, lambda: pointer["value"])
        store = _FlipAfterSearch(inner, pointer, BUILDING)
        embed = _RecordingEmbed()
        rag, reply = _asking_rag(store, embed, {SECTION_FIRE: LOCAL_FIRE}, _KeepRank())
        rag.ask(QUERY)
        # search 返回后指针已切走。拼父节若再读库，这次就会拿到新版本。
        self.assertEqual(pointer["value"], BUILDING)
        self.assertEqual(store.visible_reads, 1)
        self.assertEqual(embed.calls, 1)
        first = _new_parents(self, reply, 0)
        self.assertEqual([piece.text for piece in first], [OLD_PARENT])
        before = len(reply.parents)
        pointer["value"] = BUILDING
        rag.ask(QUERY)
        # 引用校验失败会用同一批父节再生成一次。取这次提问的第一份记录。
        second = _new_parents(self, reply, before)
        self.assertEqual([piece.text for piece in second], [NEW_PARENT])
        self.assertNotIn(OLD_PARENT, second[0].text)
        self.assertNotIn(LOCAL_FIRE, second[0].text)
        self.assertEqual(store.visible_reads, 2)
        self.assertEqual(embed.calls, 2)

    def test_ask_joins_published_chunks_not_startup_text(self) -> None:
        conn = FakeConn()
        conn.committed = [
            _published_row(PUBLISHED, FIRE_LATE, FIRE_CHUNK_LATE, SECTION_FIRE),
            _published_row(PUBLISHED, FIRE_EARLY, FIRE_CHUNK_EARLY, SECTION_FIRE),
            _published_row(PUBLISHED, ORPHAN_BODY, "orphan", ""),
            _published_row(
                PUBLISHED,
                OWL_BODY,
                OWL_CHUNK,
                SECTION_OWL,
                _vector(0.0, 1.0),
            ),
        ]
        store = PgChunkIndex(lambda: conn, lambda: PUBLISHED)
        embed = _RecordingEmbed()
        local = {SECTION_FIRE: LOCAL_FIRE, SECTION_OWL: LOCAL_OWL}
        rag, reply = _asking_rag(
            store,
            embed,
            local,
            _SectionOrder((SECTION_FIRE, SECTION_OWL)),
        )
        rag.ask(QUERY)
        self.assertEqual(embed.calls, 1)
        parents = _new_parents(self, reply, 0)
        self.assertEqual(
            [piece.text for piece in parents],
            [f"{FIRE_EARLY}\n\n{FIRE_LATE}", OWL_BODY],
        )
        joined = "\n".join(piece.text for piece in parents)
        self.assertNotIn(LOCAL_FIRE, joined)
        self.assertNotIn(LOCAL_OWL, joined)
        self.assertNotIn(ORPHAN_BODY, joined)


class _FlipAfterSearch:
    """检索读完就把指针拨走，用来抓住拼父节时的第二次读取。"""

    def __init__(self, inner: PgChunkIndex, pointer: dict[str, str], nxt: str) -> None:
        self.inner = inner
        self.pointer = pointer
        self.nxt = nxt
        self.visible_reads = 0

    def visible_chunks(
        self,
        rule_version: str,
        enabled_source_ids: Sequence[str],
    ) -> list[RagPiece]:
        self.visible_reads += 1
        return self.inner.visible_chunks(rule_version, enabled_source_ids)

    def search(
        self,
        rule_version: str,
        enabled_source_ids: Sequence[str],
        query_vector: Sequence[float],
        limit: int,
    ) -> list[RagPiece]:
        found = self.inner.search(
            rule_version,
            enabled_source_ids,
            query_vector,
            limit,
        )
        self.pointer["value"] = self.nxt
        return found


class _KeepRank:
    def rerank(self, question: str, pieces: list[RagPiece]) -> list[RagPiece]:
        del question
        return list(pieces)


class _SectionOrder:
    def __init__(self, order: tuple[str, ...]) -> None:
        self.order = order

    def rerank(self, question: str, pieces: list[RagPiece]) -> list[RagPiece]:
        del question
        return sorted(pieces, key=self._rank)

    def _rank(self, piece: RagPiece) -> int:
        section_id = piece.metadata.get("section_id", "")
        if section_id in self.order:
            return self.order.index(section_id)
        return len(self.order)


class _RecordParents:
    def __init__(self) -> None:
        self.parents: list[list[RagPiece]] = []

    def answer(
        self,
        question: str,
        parents: list[RagPiece],
        card: object = None,
    ) -> str:
        del question, card
        self.parents.append(list(parents))
        return "不是引用"


def _asking_rag(
    store: object,
    embed: _RecordingEmbed,
    local_parents: dict[str, str],
    reranker: object,
) -> tuple[ElderBrainRag, _RecordParents]:
    rag = ElderBrainRag()
    rag.retrieval = PublishedHybridSearch(
        store,
        embed.embed_query,
        lambda: (SOURCE_ID,),
    )
    rag.reranker = reranker
    reply = _RecordParents()
    rag.generation = reply
    rag.data.parents = {
        section_id: RagPiece(
            text,
            {
                "section_id": section_id,
                "entry_id": "srd51:spell:fireball",
                "rule_version": RULE_VERSION,
            },
        )
        for section_id, text in local_parents.items()
    }
    return rag, reply


def _new_parents(
    case: unittest.TestCase,
    reply: _RecordParents,
    index: int,
) -> list[RagPiece]:
    case.assertGreater(len(reply.parents), index)
    return reply.parents[index]


def _published_row(
    index_version: str,
    body: str,
    chunk_id: str,
    section_id: str,
    embedding: tuple[float, ...] | None = None,
) -> dict[str, object]:
    return {
        "index_version": index_version,
        "chunk_id": chunk_id,
        "rule_version": RULE_VERSION,
        "source_id": SOURCE_ID,
        "body": body,
        "metadata": {
            "chunk_id": chunk_id,
            "section_id": section_id,
            "entry_id": "srd51:spell:fireball",
            "rule_version": RULE_VERSION,
            "source_id": SOURCE_ID,
        },
        "embedding": list(embedding or _vector(1.0)),
    }


class DemoStartupTests(unittest.TestCase):
    def test_missing_database_url_does_not_build_faiss(self) -> None:
        removed = {DATABASE_URL_ENV: ""}
        with mock.patch.dict(os.environ, removed, clear=False):
            with mock.patch.object(IndexConstructionModule, "build", side_effect=_built):
                with mock.patch("demo_server._selected_rag", side_effect=_built) as selected:
                    with mock.patch("demo_server.start_idle_scan", side_effect=_built):
                        with self.assertRaises(ValueError) as caught:
                            main()
        self.assertIn(DATABASE_URL_ENV, str(caught.exception))
        selected.assert_not_called()

    def test_startup_uses_published_store_without_faiss(self) -> None:
        rag = ElderBrainRag()
        rag.index.embeddings = _RecordingEmbed()
        rag.data.load = _no_chunks
        auth = mock.Mock()
        url = "postgresql://db.example/elderbrain"
        with mock.patch.dict(os.environ, {DATABASE_URL_ENV: url}, clear=False):
            with mock.patch("demo_server._selected_rag", return_value=rag):
                with mock.patch("demo_server.open_auth", return_value=auth):
                    with mock.patch.object(PgChunkIndex, "ensure_schema"):
                        with mock.patch.object(
                            IndexConstructionModule,
                            "build",
                            side_effect=_built,
                        ):
                            opened, opened_auth = open_published_demo()
        self.assertIs(opened, rag)
        self.assertIs(opened_auth, auth)
        self.assertIsInstance(rag.retrieval, PublishedHybridSearch)


def _built(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("建了 FAISS")


def _no_chunks() -> list[RagPiece]:
    return []


if __name__ == "__main__":
    unittest.main()
