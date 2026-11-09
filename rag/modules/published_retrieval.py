"""已发布索引上的向量检索，再和同一批块做 BM25 融合。"""

from collections.abc import Callable, Sequence
from typing import Protocol

from admin_store import source_is_enabled
from auth_db import AuthDb
from config import RULE_VERSION, SOURCE_ID
from modules.data_preparation import RagPiece
from modules.retrieval import CANDIDATE_K, bm25_hits, fuse_hits

EmbedQuery = Callable[[str], Sequence[float]]
EnabledSources = Callable[[], Sequence[str]]
# 与块清单的父节拼法相同：只按最后一个 # 后的序号排序。
_CHUNK_INDEX_SPLIT = 1
_PARENT_DOC_TYPE = "parent"
_PARENT_GAP = "\n\n"


class PublishedChunks(Protocol):
    """向量库要提供的两个读法。查询不能自己指定索引版本。"""

    def visible_chunks(
        self,
        rule_version: str,
        enabled_source_ids: Sequence[str],
    ) -> list[RagPiece]:
        """当前已发布版本里允许看到的块。"""

    def search(
        self,
        rule_version: str,
        enabled_source_ids: Sequence[str],
        query_vector: Sequence[float],
        limit: int,
    ) -> list[RagPiece]:
        """已发布版本中的余弦近邻。"""


class PublishedHybridSearch:
    """向量走已发布库，BM25 只用同一次过滤后的块。"""

    def __init__(
        self,
        store: PublishedChunks,
        embed_query: EmbedQuery,
        enabled_source_ids: EnabledSources,
        rule_version: str = RULE_VERSION,
    ) -> None:
        """store 自己读取已发布指针。embed_query 只在确实有可见块时调用。"""
        self._store = store
        self._embed_query = embed_query
        self._enabled_source_ids = enabled_source_ids
        self._rule_version = rule_version

    def hybrid_search(self, query: str) -> list[RagPiece]:
        """融合向量和 BM25。没有可见块时返回空列表，且不调用嵌入。

        来源可以在进程运行中被停用，所以每次都重新取可见块，不缓存启动时的全文。
        """
        hits, _published = self.search_with_published(query)
        return hits

    def search_with_published(
        self,
        query: str,
    ) -> tuple[list[RagPiece], tuple[RagPiece, ...]]:
        """返回命中，以及这次读到的已发布块。

        父节必须用这份块。函数返回后指针可能已切走，不能再读库，也不能再嵌入。
        """
        if not isinstance(query, str) or not query.strip():
            return [], ()
        enabled = self._enabled()
        visible = tuple(self._store.visible_chunks(self._rule_version, enabled))
        if not visible:
            return [], ()
        vector_hits = self._store.search(
            self._rule_version,
            enabled,
            self._embed_query(query),
            CANDIDATE_K,
        )
        hits = fuse_hits(vector_hits, bm25_hits(list(visible), query))
        return hits, visible

    def visible_count(self) -> int:
        """当前指针下允许检索的块数。指针为空或来源都停用时为 0。"""
        return len(self._visible())

    def _visible(self) -> list[RagPiece]:
        return self._store.visible_chunks(self._rule_version, self._enabled())

    def _enabled(self) -> tuple[str, ...]:
        sources = self._enabled_source_ids()
        if isinstance(sources, (str, bytes)) or not isinstance(sources, Sequence):
            raise ValueError("来源编号必须是列表")
        return tuple(sources)


def parent_sections_from_chunks(
    chunks: Sequence[RagPiece],
    hits: Sequence[RagPiece],
) -> list[RagPiece]:
    """按命中顺序交出父节。正文来自这次的已发布块，不读启动时的清单。"""
    grouped = _chunks_by_section(chunks)
    return _parents_in_hit_order(grouped, hits)


def _chunks_by_section(chunks: Sequence[RagPiece]) -> dict[str, list[RagPiece]]:
    grouped: dict[str, list[RagPiece]] = {}
    for piece in chunks:
        section_id = _section_id(piece)
        # 没有节号的块不能自己变成一节，否则引用会对不上条目。
        if not section_id:
            continue
        grouped.setdefault(section_id, []).append(piece)
    return grouped


def _parents_in_hit_order(
    grouped: dict[str, list[RagPiece]],
    hits: Sequence[RagPiece],
) -> list[RagPiece]:
    ordered: list[RagPiece] = []
    seen: set[str] = set()
    for hit in hits:
        section_id = _section_id(hit)
        if not section_id or section_id in seen:
            continue
        seen.add(section_id)
        pieces = grouped.get(section_id)
        if pieces is None:
            continue
        ordered.append(_joined_parent(section_id, pieces))
    return ordered


def _joined_parent(section_id: str, pieces: list[RagPiece]) -> RagPiece:
    ordered = sorted(pieces, key=_chunk_index)
    metadata = dict(ordered[0].metadata)
    metadata["section_id"] = section_id
    metadata["doc_type"] = _PARENT_DOC_TYPE
    return RagPiece(text=_PARENT_GAP.join(piece.text for piece in ordered), metadata=metadata)


def _section_id(piece: RagPiece) -> str:
    return piece.metadata.get("section_id", "").strip()


def _chunk_index(piece: RagPiece) -> int:
    chunk_id = piece.metadata.get("chunk_id", "")
    suffix = chunk_id.rsplit("#", _CHUNK_INDEX_SPLIT)[-1]
    return int(suffix)


def enabled_source_ids(db: AuthDb) -> tuple[str, ...]:
    """仍参与检索的来源编号。

    没有停用记录时，第一期来源仍启用。停用后返回空元组，正文不再进入 BM25。
    """
    if source_is_enabled(db, SOURCE_ID):
        return (SOURCE_ID,)
    return ()
