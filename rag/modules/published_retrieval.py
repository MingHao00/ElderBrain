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
        if not isinstance(query, str) or not query.strip():
            return []
        enabled = self._enabled()
        visible = self._store.visible_chunks(self._rule_version, enabled)
        if not visible:
            return []
        vector_hits = self._store.search(
            self._rule_version,
            enabled,
            self._embed_query(query),
            CANDIDATE_K,
        )
        return fuse_hits(vector_hits, bm25_hits(visible, query))

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


def enabled_source_ids(db: AuthDb) -> tuple[str, ...]:
    """仍参与检索的来源编号。

    没有停用记录时，第一期来源仍启用。停用后返回空元组，正文不再进入 BM25。
    """
    if source_is_enabled(db, SOURCE_ID):
        return (SOURCE_ID,)
    return ()
