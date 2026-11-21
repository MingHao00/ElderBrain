"""一轮内钉住已发布索引，检索时只返回父节的条目、标题、正文和分数。"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from modules.data_preparation import RagPiece
from modules.retrieval import RRF_K

PARENT_DOC_TYPE = "parent"
PATH_SEPARATOR = " > "
META_DOC_TYPE = "doc_type"
META_ENTRY_ID = "entry_id"
META_ENTRY_PATH = "entry_path"
META_TITLE = "title"
META_RELEVANCE_SCORE = "relevance_score"
BLANK = ""

ReadPublished = Callable[[], str | None]
SearchParents = Callable[[str, str], Sequence[RagPiece]]


@dataclass(frozen=True, slots=True)
class PinnedIndex:
    """一轮开始时抄下的已发布版本。空字符串表示当时还没有发布版本。"""

    index_version: str


@dataclass(frozen=True, slots=True)
class ParentHit:
    """交给调用方的一节父节。不含子块、向量和整份 metadata。"""

    entry_id: str
    title: str
    text: str
    score: float


def pin_published_index(read_published: ReadPublished) -> PinnedIndex:
    """读取当前已发布版本并钉住，只调用 read_published 一次。

    read_published 返回当时的版本号。还没有发布记录时返回 None 或空白。
    返回值不可变。没有版本时 index_version 为空字符串，不抛异常。
    版本号被抄进返回值，后面的检索不再读活指针。
    """
    return PinnedIndex(_version_text(read_published()))


def rag_search(
    pinned: PinnedIndex,
    search_parents: SearchParents,
    model_query: str,
    question: str,
) -> list[ParentHit]:
    """用钉住的版本检索父节。检索时不再读取活的已发布指针。

    model_query 是调用方传入的模型查询词。去掉首尾空白后为空则改用 question。
    两边都是空白，或钉住的版本为空时，不调用 search_parents，返回空列表。
    search_parents 接收查询词和钉住的版本号，返回父节序列。
    doc_type 不是 parent 的块会被丢掉。
    """
    query = _query_text(model_query, question)
    if not query or not pinned.index_version:
        return []
    return _hits(search_parents(query, pinned.index_version))


def _version_text(raw: str | None) -> str:
    if raw is None:
        return BLANK
    return raw.strip()


def _query_text(model_query: str, question: str) -> str:
    written = model_query.strip()
    if written:
        return written
    return question.strip()


def _hits(pieces: Sequence[RagPiece]) -> list[ParentHit]:
    parents = [piece for piece in pieces if _is_parent(piece)]
    return [_hit(piece, rank) for rank, piece in enumerate(parents)]


def _is_parent(piece: RagPiece) -> bool:
    return piece.metadata.get(META_DOC_TYPE) == PARENT_DOC_TYPE


def _hit(piece: RagPiece, rank: int) -> ParentHit:
    metadata = piece.metadata
    return ParentHit(
        entry_id=metadata.get(META_ENTRY_ID, BLANK),
        title=_title(metadata),
        text=piece.text,
        score=_score(metadata, rank),
    )


def _title(metadata: Mapping[str, str]) -> str:
    named = metadata.get(META_TITLE, BLANK).strip()
    if named:
        return named
    return _path_title(metadata.get(META_ENTRY_PATH, BLANK))


def _path_title(entry_path: str) -> str:
    """没有 title 时用路径最后一段，不从正文里摘标题。"""
    path = entry_path.strip()
    if not path:
        return BLANK
    return path.split(PATH_SEPARATOR)[-1].strip()


def _score(metadata: Mapping[str, str], rank: int) -> float:
    """有可解析的 relevance_score 就用它，否则按父节顺序给 1/(RRF_K+rank)。

    现有 fuse_hits 不把融合分数写回块上，所以缺分数时用 retrieval.RRF_K。
    rank 从 0 起，只数保留下来的父节。
    """
    parsed = _parsed_score(metadata.get(META_RELEVANCE_SCORE))
    if parsed is not None:
        return parsed
    return 1.0 / (RRF_K + rank)


def _parsed_score(raw: str | None) -> float | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None
