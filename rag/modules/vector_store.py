"""PostgreSQL + pgvector 里的块向量。

服务只按已发布指针读取。写入新版本不改指针，指针仍在 SQLite。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Protocol, TypeVar

from modules.api_embeddings import EMBEDDING3_DIMENSIONS
from modules.data_preparation import RagPiece
from modules.vector_rows import (
    ChunkVector,
    _chunk_rows,
    _require_limit,
    _require_rule,
    _require_sources,
    _require_vector,
    _require_version,
    _string_map,
    _vector_literal,
)

DATABASE_URL_ENV = "DATABASE_URL"
_ROW_BODY = "body"
_ROW_METADATA = "metadata"

# 语句文本是常量。版本号、正文和向量只走占位符。
# 来源列表在驱动里没有类型 oid，::text[] 让数据库解释，值仍在参数里。
CREATE_EXTENSION_SQL = "CREATE EXTENSION IF NOT EXISTS vector"
DELETE_VERSION_SQL = "DELETE FROM chunk_vectors WHERE index_version = %s"
INSERT_CHUNK_SQL = (
    "INSERT INTO chunk_vectors ("
    "index_version, chunk_id, rule_version, source_id, body, metadata, embedding"
    ") VALUES (%s, %s, %s, %s, %s, %s, %s::vector)"
)
SEARCH_SQL = (
    "SELECT body, metadata FROM chunk_vectors "
    "WHERE index_version = %s AND rule_version = %s "
    "AND source_id = ANY(%s::text[]) "
    "ORDER BY embedding <=> %s::vector LIMIT %s"
)
VISIBLE_SQL = (
    "SELECT body, metadata FROM chunk_vectors "
    "WHERE index_version = %s AND rule_version = %s "
    "AND source_id = ANY(%s::text[])"
)

_T = TypeVar("_T")
PublishedVersion = Callable[[], str | None]


class _SqlCursor(Protocol):
    def fetchall(self) -> Sequence[Mapping[str, object]]:
        """返回本次语句的全部行。"""


class _SqlConnection(Protocol):
    def execute(
        self,
        query: str,
        params: Sequence[object] | None = None,
    ) -> _SqlCursor:
        """执行一条语句。参数用驱动占位符绑定。"""

    def executemany(
        self,
        query: str,
        params_seq: Sequence[Sequence[object]],
    ) -> None:
        """用同一条语句写入多行。"""

    def transaction(self) -> AbstractContextManager[object]:
        """开始一个事务。失败时回滚。"""

    def __enter__(self) -> "_SqlConnection":
        """进入连接。"""

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        """离开连接。"""


class PgChunkIndex:
    """按 index_version 保存块，并只检索已发布的那一版。"""

    def __init__(
        self,
        connect: Callable[[], _SqlConnection],
        published_version: PublishedVersion,
    ) -> None:
        """connect 每次操作新建连接。published_version 读取当前指针，不负责发布。"""
        self._connect = connect
        self._published_version = published_version

    def ensure_schema(self) -> None:
        """确保 pgvector 扩展和块表存在。重复调用不改已有行，也不动指针。"""
        def work(conn: _SqlConnection) -> None:
            conn.execute(CREATE_EXTENSION_SQL)
            conn.execute(_create_table_sql())

        _in_transaction(self._connect, work)

    def replace_version(self, index_version: str, chunks: Sequence[ChunkVector]) -> None:
        """替换一个索引版本的全部分块。不修改已发布指针。

        删除和插入在同一个事务里。任一失败则该版本保持原样。
        版本号格式不符、正文为空或向量维度不对时抛出 ValueError，且不连接。
        """
        version = _require_version(index_version)
        rows = _chunk_rows(version, chunks)

        def work(conn: _SqlConnection) -> None:
            conn.execute(DELETE_VERSION_SQL, (version,))
            if rows:
                conn.executemany(INSERT_CHUNK_SQL, rows)

        # 指针在另一个库。这里没有发布函数可调用。
        _in_transaction(self._connect, work)

    def delete_version(self, index_version: str) -> None:
        """删除一个索引版本的全部分块，只提交一次。

        其他版本保留。版本号格式不符时抛出 ValueError。
        """
        version = _require_version(index_version)

        def work(conn: _SqlConnection) -> None:
            conn.execute(DELETE_VERSION_SQL, (version,))

        _in_transaction(self._connect, work)

    def visible_chunks(
        self,
        rule_version: str,
        enabled_source_ids: Sequence[str],
    ) -> list[RagPiece]:
        """已发布版本中，规则版本和来源都允许的全部块。

        指针为空时返回空列表，不打开连接，因此不会读到构建中的版本。
        """
        return self._read(VISIBLE_SQL, rule_version, enabled_source_ids, None, None)

    def search(
        self,
        rule_version: str,
        enabled_source_ids: Sequence[str],
        query_vector: Sequence[float],
        limit: int,
    ) -> list[RagPiece]:
        """按余弦距离返回已发布版本中的近邻。

        距离使用 pgvector 的 <=>。rule_version 与来源在 SQL 里过滤。
        指针为空时返回空列表。维度或条数不合法时抛出 ValueError，且不连接。
        """
        vector = _vector_literal(_require_vector(query_vector))
        bound = _require_limit(limit)
        return self._read(SEARCH_SQL, rule_version, enabled_source_ids, vector, bound)

    def _read(
        self,
        sql: str,
        rule_version: str,
        enabled_source_ids: Sequence[str],
        vector: str | None,
        limit: int | None,
    ) -> list[RagPiece]:
        published = _published_text(self._published_version)
        if published is None:
            return []
        params = _read_params(
            published,
            _require_rule(rule_version),
            _require_sources(enabled_source_ids),
            vector,
            limit,
        )

        def work(conn: _SqlConnection) -> list[RagPiece]:
            return _pieces(conn.execute(sql, params))

        return _in_transaction(self._connect, work)


def connect_postgres(database_url: str) -> Callable[[], _SqlConnection]:
    """按连接串建立 psycopg 连接。连接串不写入异常文本。

    返回的函数每次调用打开一条新连接。连接串为空时抛出 ValueError。
    数据库拒绝连接时抛出 ConnectionError，且不附带连接串。
    """
    if not isinstance(database_url, str) or not database_url.strip():
        raise ValueError(f"请设置 {DATABASE_URL_ENV} 环境变量")

    def open_connection() -> _SqlConnection:
        return _psycopg_connection(database_url)

    return open_connection


def _psycopg_connection(database_url: str) -> _SqlConnection:
    import psycopg
    from psycopg.rows import dict_row

    # 失败信息里常带着整段连接串。换成类型名，避免密码进日志。
    try:
        conn = psycopg.connect(database_url, row_factory=dict_row)
    except Exception as exc:
        raise ConnectionError(f"PostgreSQL 连接失败：{type(exc).__name__}") from None
    try:
        _register_json(conn)
    except Exception as exc:
        conn.close()
        raise ConnectionError(f"PostgreSQL 连接失败：{type(exc).__name__}") from None
    return conn


def _register_json(conn: object) -> None:
    # 这条连接只写块元数据。dict 直接绑成 jsonb，测试替身仍看到普通字典。
    from psycopg.types.json import JsonbDumper

    adapters = getattr(conn, "adapters", None)
    if adapters is None:
        raise ConnectionError("PostgreSQL 连接失败：缺少类型适配")
    adapters.register_dumper(dict, JsonbDumper)


def _in_transaction(
    connect: Callable[[], _SqlConnection],
    work: Callable[[_SqlConnection], _T],
) -> _T:
    with connect() as conn:
        with conn.transaction():
            return work(conn)


def _create_table_sql() -> str:
    # vector(n) 的 n 不能做占位符。数字只来自嵌入常量，不是用户输入。
    if type(EMBEDDING3_DIMENSIONS) is not int or EMBEDDING3_DIMENSIONS < 1:
        raise ValueError("嵌入维度必须是正整数")
    dimension = str(EMBEDDING3_DIMENSIONS)
    return (
        "CREATE TABLE IF NOT EXISTS chunk_vectors ("
        "index_version TEXT NOT NULL, "
        "chunk_id TEXT NOT NULL, "
        "rule_version TEXT NOT NULL, "
        "source_id TEXT NOT NULL, "
        "body TEXT NOT NULL, "
        "metadata JSONB NOT NULL, "
        "embedding vector(" + dimension + ") NOT NULL, "
        "PRIMARY KEY (index_version, chunk_id))"
    )


def _read_params(
    published: str,
    rule_version: str,
    sources: Sequence[str],
    vector: str | None,
    limit: int | None,
) -> tuple[object, ...]:
    params: list[object] = [published, rule_version, list(sources)]
    if vector is not None:
        params.append(vector)
    if limit is not None:
        params.append(limit)
    return tuple(params)


def _pieces(cursor: _SqlCursor) -> list[RagPiece]:
    return [_piece_row(row) for row in cursor.fetchall()]


def _piece_row(row: Mapping[str, object]) -> RagPiece:
    body = row.get(_ROW_BODY)
    raw = row.get(_ROW_METADATA)
    if not isinstance(body, str) or not isinstance(raw, Mapping):
        raise ValueError("块行格式不符")
    return RagPiece(text=body, metadata=_string_map(raw))


def _published_text(read: PublishedVersion) -> str | None:
    value = read()
    if not isinstance(value, str) or not value.strip():
        return None
    return value

