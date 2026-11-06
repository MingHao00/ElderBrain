"""向量检索加 BM25，再用倒数排名融合。做法来自 All-in-RAG 第 8 章。"""

import hashlib
import re

from langchain_community.retrievers import BM25Retriever
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

from modules.data_preparation import RagPiece
from modules.index_construction import _as_documents

RRF_K = 60
# 设计里重排前的候选起点是 20。先多取一些，再交给重排模型。
CANDIDATE_K = 20
# rank_bm25 在全 0 时仍按名次返回。小于等于这个值的块不参与融合。
BM25_SCORE_FLOOR = 0
# 与 offline 的 cjk-word-v1 相同，但不引用 offline。
_CJK_OR_WORD = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9]+")


def tokenize_cjk(text: str) -> list[str]:
    """每个汉字一个词，每段连续英文或数字一个词。

    LangChain 默认 preprocess 是 str.split()。中文问句没有空格，整句会变成一个词。
    """
    return _CJK_OR_WORD.findall(text)


class RetrievalOptimizationModule:
    """混合检索只查中文 5.1。"""

    def __init__(self, vectorstore: FAISS, children: list[RagPiece]) -> None:
        self.vectorstore = vectorstore
        self.children = children
        documents = _as_documents(children)
        self.vector_retriever = vectorstore.as_retriever(
            search_type="similarity",
            search_kwargs={"k": CANDIDATE_K},
        )
        self.bm25_retriever = BM25Retriever.from_documents(
            documents,
            preprocess_func=tokenize_cjk,
            k=CANDIDATE_K,
        )

    def hybrid_search(self, query: str) -> list[RagPiece]:
        """两路融合后，每个父节只留排名最高的一块，交给重排。"""
        vector_docs = self.vector_retriever.invoke(query)
        bm25_docs = _positive_bm25(self.bm25_retriever, query)
        fused = [_piece(doc) for doc in _rrf(vector_docs, bm25_docs)]
        return _one_per_section(fused)


def _positive_bm25(retriever: BM25Retriever, query: str) -> list[Document]:
    """只保留 BM25 正分文档。

    本机 BM25Retriever.invoke 调用 vectorizer.get_top_n，不带回分数。
    分数在 BM25Okapi.get_scores 上，顺序与 retriever.docs 对齐。
    """
    tokens = retriever.preprocess_func(query)
    scores = retriever.vectorizer.get_scores(tokens)
    ranked = sorted(
        range(len(scores)),
        key=lambda index: float(scores[index]),
        reverse=True,
    )
    selected: list[Document] = []
    for index in ranked:
        if float(scores[index]) <= BM25_SCORE_FLOOR:
            break
        selected.append(retriever.docs[index])
        if len(selected) >= retriever.k:
            break
    return selected


def _rrf(vector_docs: list[Document], bm25_docs: list[Document]) -> list[Document]:
    scores: dict[str, float] = {}
    found: dict[str, Document] = {}
    _add_scores(vector_docs, scores, found)
    _add_scores(bm25_docs, scores, found)
    ranked = sorted(scores, key=lambda doc_id: scores[doc_id], reverse=True)
    return [found[doc_id] for doc_id in ranked]


def _add_scores(
    docs: list[Document],
    scores: dict[str, float],
    found: dict[str, Document],
) -> None:
    for rank, doc in enumerate(docs):
        doc_id = hashlib.md5(doc.page_content.encode("utf-8")).hexdigest()
        found[doc_id] = doc
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (RRF_K + rank + 1)


def _one_per_section(pieces: list[RagPiece]) -> list[RagPiece]:
    kept: list[RagPiece] = []
    seen: set[str] = set()
    for piece in pieces:
        section_id = piece.metadata.get("section_id", "")
        if section_id in seen:
            continue
        seen.add(section_id)
        kept.append(piece)
    return kept


def _piece(doc: Document) -> RagPiece:
    metadata = {key: str(value) for key, value in doc.metadata.items()}
    return RagPiece(text=doc.page_content, metadata=metadata)
