"""把中文 5.1 子块写入一个新的向量版本。

登记仍由 rebuild_index 完成。这里不调用发布，也不改已发布指针。
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Collection, Sequence
from datetime import datetime, timezone
from pathlib import Path

from admin_store import (
    INDEX_VERSION_MAX_CHARS,
    INDEX_VERSION_MIN_CHARS,
    ensure_tables,
    rebuild_index,
)
from auth_db import AuthDb
from config import RULE_VERSION, SOURCE_ID
from modules.api_embeddings import EMBEDDING3_DIMENSIONS
from modules.data_preparation import DataPreparationModule, RagPiece
from modules.vector_rows import ChunkVector

VERSION_PREFIX = "idx"
STAMP_FORMAT = "%Y%m%d%H%M%S"
# token_hex 的字节数。两位十六进制对应一个字节，后缀长度由此得出。
SUFFIX_BYTES = 4
SUFFIX_HEX_CHARS = SUFFIX_BYTES * 2
# 同一秒里后缀撞车时再试。试完仍撞上已登记版本就失败，不写向量。
VERSION_ATTEMPTS = 8
ERR_CATALOG_EMPTY = "块清单为空"
ERR_CATALOG_SOURCE = "块来源不符"
ERR_EMBED_COUNT = "嵌入条数不符"
ERR_EMBED_DIMENSION = "嵌入维度不符"
ERR_VERSION_ALLOC = "无法分配索引版本"
ERR_VERSION_SHAPE = "索引版本格式不符"
_VERSION_MARKS = frozenset("._-")

EmbedDocuments = Callable[[list[str]], Sequence[Sequence[float]]]
ReplaceVersion = Callable[[str, Sequence[ChunkVector]], None]
DropVersion = Callable[[str], None]
SuffixFn = Callable[[], str]
Clock = Callable[[], datetime]
TakenVersions = Callable[[], Collection[str]]


def build_srd_index(
    chunk_file: Path,
    embed_documents: EmbedDocuments,
    replace_version: ReplaceVersion,
    taken_versions: Collection[str],
    suffix: SuffixFn | None = None,
    clock: Clock | None = None,
) -> str:
    """读子块、嵌入并替换出一个新版本。成功后才返回版本号。

    清单为空、文件不存在或来源不符时不嵌入、不写库。
    嵌入条数或维度不对时不调用 replace_version。
    嵌入或替换抛错时不返回版本，调用方因此不会登记。
    """
    pieces = _accepted_pieces(chunk_file)
    version = _fresh_version(taken_versions, _now(clock), _pick_suffix(suffix))
    vectors = _embedded(pieces, embed_documents)
    replace_version(version, _as_chunks(pieces, vectors))
    return version


def rebuild_registered(
    db: AuthDb,
    actor_id: str,
    builder: Callable[[], str] | None,
    drop_version: DropVersion | None,
    now: datetime,
) -> str:
    """登记构建结果。登记失败时撤掉刚写入的向量。

    不调用嵌入。已发布指针仍只由 publish_index 改动。
    """
    written: list[str] = []
    try:
        return rebuild_index(db, actor_id, _capture(builder, written), now)
    except Exception:
        _drop_written(drop_version, written)
        raise


def taken_index_versions(db: AuthDb) -> set[str]:
    """读出已登记的版本，供新版本避开。不写已发布指针。"""
    with db.immediate() as conn:
        ensure_tables(conn)
        rows = conn.execute("SELECT index_version FROM index_builds").fetchall()
    return {str(row["index_version"]) for row in rows}


def make_demo_builder(
    chunk_file: Path,
    embed_documents: EmbedDocuments,
    replace_version: ReplaceVersion,
    taken_versions: TakenVersions,
) -> Callable[[], str]:
    """演示进程的 index_builder。构造时不读清单、不嵌入、不写库。"""

    def build() -> str:
        return build_srd_index(
            chunk_file,
            embed_documents,
            replace_version,
            taken_versions(),
        )

    return build


def _accepted_pieces(chunk_file: Path) -> tuple[RagPiece, ...]:
    pieces = tuple(DataPreparationModule(chunk_file).load())
    if not pieces:
        raise ValueError(ERR_CATALOG_EMPTY)
    if any(not _is_srd(piece) for piece in pieces):
        raise ValueError(ERR_CATALOG_SOURCE)
    return pieces


def _is_srd(piece: RagPiece) -> bool:
    meta = piece.metadata
    rule = meta.get("rule_version")
    source = meta.get("source_id")
    return rule == RULE_VERSION and source == SOURCE_ID


def _fresh_version(taken: Collection[str], now: datetime, suffix: SuffixFn) -> str:
    occupied = set(taken)
    for _attempt in range(VERSION_ATTEMPTS):
        candidate = _candidate(now, suffix())
        if candidate not in occupied:
            return candidate
    raise ValueError(ERR_VERSION_ALLOC)


def _candidate(now: datetime, suffix: str) -> str:
    if not _suffix_ok(suffix):
        raise ValueError(ERR_VERSION_SHAPE)
    version = f"{VERSION_PREFIX}-{now.strftime(STAMP_FORMAT)}-{suffix}"
    if not _version_ok(version):
        raise ValueError(ERR_VERSION_SHAPE)
    return version


def _suffix_ok(value: str) -> bool:
    if len(value) != SUFFIX_HEX_CHARS:
        return False
    return all(ch.isascii() and ch.isalnum() for ch in value)


def _version_ok(value: str) -> bool:
    # 形状与管理端指针相同，写进去的版本以后还能被发布。
    length = len(value)
    if length < INDEX_VERSION_MIN_CHARS or length > INDEX_VERSION_MAX_CHARS:
        return False
    if not value[0].isascii() or not value[0].isalpha():
        return False
    return all(_version_char(ch) for ch in value)


def _version_char(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch in _VERSION_MARKS)


def _embedded(
    pieces: Sequence[RagPiece],
    embed_documents: EmbedDocuments,
) -> tuple[tuple[float, ...], ...]:
    texts = [piece.text for piece in pieces]
    raw = tuple(embed_documents(texts))
    if len(raw) != len(texts):
        raise ValueError(ERR_EMBED_COUNT)
    return tuple(_one_vector(item) for item in raw)


def _one_vector(value: object) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(ERR_EMBED_DIMENSION)
    numbers = tuple(value)
    if len(numbers) != EMBEDDING3_DIMENSIONS:
        raise ValueError(ERR_EMBED_DIMENSION)
    return tuple(float(item) for item in numbers)


def _as_chunks(
    pieces: Sequence[RagPiece],
    vectors: Sequence[tuple[float, ...]],
) -> tuple[ChunkVector, ...]:
    paired = zip(pieces, vectors)
    return tuple(ChunkVector(piece, vector) for piece, vector in paired)


def _now(clock: Clock | None) -> datetime:
    if clock is None:
        return datetime.now(timezone.utc)
    return clock()


def _pick_suffix(suffix: SuffixFn | None) -> SuffixFn:
    if suffix is None:
        return _random_suffix
    return suffix


def _random_suffix() -> str:
    return secrets.token_hex(SUFFIX_BYTES)


def _capture(
    builder: Callable[[], str] | None,
    written: list[str],
) -> Callable[[], str] | None:
    # None 原样交给 rebuild_index，才能报「索引构建未配置」。
    if builder is None:
        return None

    def capture() -> str:
        version = builder()
        written.append(version)
        return version

    return capture


def _drop_written(drop_version: DropVersion | None, written: Sequence[str]) -> None:
    if drop_version is None or not written:
        return
    drop_version(written[-1])
