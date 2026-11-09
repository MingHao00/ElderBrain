"""把一块 RagPiece 收成可绑定的参数。不连接数据库，也不改已发布指针。"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from admin_store import INDEX_VERSION_MAX_CHARS, INDEX_VERSION_MIN_CHARS
from modules.api_embeddings import EMBEDDING3_DIMENSIONS
from modules.data_preparation import RagPiece

# 拦住一次查出过多行。召回条数由检索模块另定。
MIN_SEARCH_LIMIT = 1
MAX_SEARCH_LIMIT = 100
RULE_VERSION_MAX_CHARS = 32
SOURCE_ID_MAX_CHARS = 64
CHUNK_ID_MAX_CHARS = 200
# 父节原文可能长于嵌入切片。上限只防失控输入。
BODY_MAX_CHARS = 100_000
_VERSION_MARKS = frozenset("._-")
_META_CHUNK = "chunk_id"
_META_RULE = "rule_version"
_META_SOURCE = "source_id"
_META_INDEX = "index_version"


@dataclass(frozen=True)
class ChunkVector:
    """一块已经算好的向量。维度必须与嵌入常量一致。"""

    piece: RagPiece
    embedding: tuple[float, ...]


def _chunk_rows(index_version: str, chunks: Sequence[ChunkVector]) -> list[tuple[object, ...]]:
    """把一批块收成插入参数。编号重复或字段缺失时抛出 ValueError。"""
    if isinstance(chunks, (str, bytes)) or not isinstance(chunks, Sequence):
        raise ValueError("块列表格式不符")
    rows: list[tuple[object, ...]] = []
    seen: set[str] = set()
    for chunk in chunks:
        row = _one_row(index_version, chunk)
        chunk_id = str(row[1])
        if chunk_id in seen:
            raise ValueError("同一版本里块编号重复")
        seen.add(chunk_id)
        rows.append(row)
    return rows


def _one_row(index_version: str, chunk: ChunkVector) -> tuple[object, ...]:
    if not isinstance(chunk, ChunkVector):
        raise ValueError("块必须带有向量")
    metadata = _copy_metadata(chunk.piece.metadata)
    chunk_id = _required_meta(metadata, _META_CHUNK, CHUNK_ID_MAX_CHARS)
    rule_version = _required_meta(metadata, _META_RULE, RULE_VERSION_MAX_CHARS)
    source_id = _required_meta(metadata, _META_SOURCE, SOURCE_ID_MAX_CHARS)
    _reject_conflicting_version(metadata, index_version)
    literal = _vector_literal(_require_vector(chunk.embedding))
    return (
        index_version,
        chunk_id,
        rule_version,
        source_id,
        _body(chunk.piece.text),
        metadata,
        literal,
    )


def _reject_conflicting_version(metadata: Mapping[str, str], index_version: str) -> None:
    # 清单里的空版本表示「还没发布」。只有写了另一个版本才算冲突。
    labeled = metadata.get(_META_INDEX, "")
    if labeled.strip() and labeled != index_version:
        raise ValueError("块上的索引版本与写入版本不一致")


def _copy_metadata(raw: object) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        raise ValueError("块元数据必须是字符串")
    return _string_map(raw)


def _string_map(raw: Mapping[object, object]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("块元数据必须是字符串")
        cleaned[key] = value
    return cleaned


def _required_meta(metadata: Mapping[str, str], key: str, limit: int) -> str:
    value = metadata.get(key, "")
    if not value.strip() or len(value) > limit:
        raise ValueError("块元数据不完整")
    return value


def _body(text: object) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("块正文不能为空")
    if len(text) > BODY_MAX_CHARS:
        raise ValueError("块正文过长")
    return text


def _require_version(value: object) -> str:
    """写入用的版本号。形状与管理端指针相同，调用方仍不改指针。"""
    if not isinstance(value, str) or not _version_shape(value):
        raise ValueError("索引版本格式不符")
    return value


def _version_shape(value: str) -> bool:
    # 与管理端指针接受的形状相同，这样写进去的版本以后还能被发布。
    length = len(value)
    if length < INDEX_VERSION_MIN_CHARS or length > INDEX_VERSION_MAX_CHARS:
        return False
    if not value[0].isascii() or not value[0].isalpha():
        return False
    return all(_version_char(ch) for ch in value)


def _version_char(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch in _VERSION_MARKS)


def _require_rule(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("规则版本不能为空")
    if len(value) > RULE_VERSION_MAX_CHARS:
        raise ValueError("规则版本过长")
    return value


def _require_sources(value: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("来源编号必须是列表")
    sources = tuple(value)
    for source in sources:
        if not isinstance(source, str) or not source.strip():
            raise ValueError("来源编号必须是非空字符串")
        if len(source) > SOURCE_ID_MAX_CHARS:
            raise ValueError("来源编号过长")
    return sources


def _require_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("返回条数必须是整数")
    if value < MIN_SEARCH_LIMIT or value > MAX_SEARCH_LIMIT:
        raise ValueError("返回条数超出范围")
    return value


def _require_vector(values: Sequence[float]) -> tuple[float, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError("向量格式不符")
    if len(values) != EMBEDDING3_DIMENSIONS:
        raise ValueError("向量维度不符")
    numbers: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("向量分量必须是数字")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("向量分量必须是有限数字")
        numbers.append(number)
    return tuple(numbers)


def _vector_literal(values: Sequence[float]) -> str:
    # 整段字面量作为一个绑定参数，再由 SQL 里的 ::vector 转换。
    return "[" + ",".join(repr(float(value)) for value in values) + "]"
