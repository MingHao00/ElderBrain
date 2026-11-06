import unittest

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from modules.data_preparation import RagPiece
from modules.retrieval import RetrievalOptimizationModule

QUERY = "火球术的射程是多少"
RELEVANT = "火球术射程一百五十尺"


class _SameVector(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _text in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0]


class _NoVectorHits:
    def invoke(self, query: str) -> list[Document]:
        return []


class ChineseBm25Tests(unittest.TestCase):
    def test_unspaced_query_drops_zero_score_chunks(self) -> None:
        # 只有两篇时，出现一次的词 idf 为 0，正分样本需要第三篇垫底。
        pieces = [
            RagPiece(RELEVANT, {"section_id": "fire"}),
            RagPiece("猫头鹰", {"section_id": "owl"}),
            RagPiece("巨龙", {"section_id": "dragon"}),
        ]
        documents = [
            Document(page_content=piece.text, metadata=dict(piece.metadata))
            for piece in pieces
        ]
        store = FAISS.from_documents(documents, _SameVector())
        module = RetrievalOptimizationModule(store, pieces)
        # 向量一路不返回文档，结果里的块只能来自 BM25。
        module.vector_retriever = _NoVectorHits()
        found = [piece.text for piece in module.hybrid_search(QUERY)]
        self.assertEqual(found, [RELEVANT])


if __name__ == "__main__":
    unittest.main()
