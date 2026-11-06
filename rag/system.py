"""ElderBrain 的 RAG 主体。四段流程来自 All-in-RAG 第 8 章。"""

import argparse
import sys
from pathlib import Path

_RAG_DIR = Path(__file__).resolve().parent
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

from dotenv import load_dotenv

from config import REPO_ROOT, RagConfig
from generation_call import EXPERIMENT_TEMPERATURE
from modules.data_preparation import DataPreparationModule, RagPiece
from modules.generation import NO_EVIDENCE, GenerationIntegrationModule
from modules.index_construction import IndexConstructionModule, file_sha256
from modules.retrieval import RetrievalOptimizationModule
from modules.text_rerank import TextReranker, load_text_reranker
from scope import VERSION_SENTENCE, refusal_for

load_dotenv(REPO_ROOT / ".env")


class ElderBrainRag:
    """加载块清单、建索引、混合检索、按父节生成。"""

    def __init__(self, config: RagConfig | None = None) -> None:
        self.config = config or RagConfig()
        self.data = DataPreparationModule(self.config.chunk_file)
        self.index = IndexConstructionModule(self.config.index_dir)
        self.retrieval: RetrievalOptimizationModule | None = None
        self.reranker: TextReranker | None = None
        self.generation: GenerationIntegrationModule | None = None

    def build_knowledge_base(self) -> int:
        """有对得上的本地索引就加载，没有就从块清单重建。返回子块数量。"""
        children = self.data.load()
        chunk_sha256 = _chunk_sha256(self.config.chunk_file)
        vectorstore = self.index.load(chunk_sha256, self.index.embedding_model_name())
        if vectorstore is None:
            vectorstore = self.index.build(children)
            self.index.save(chunk_sha256)
        self.retrieval = RetrievalOptimizationModule(vectorstore, children)
        return len(children)

    def ask(
        self,
        question: str,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """版本和提示注入都在检索之前返回。检索只看这一句。"""
        refused = refusal_for(question)
        if refused is not None:
            return refused
        if self.retrieval is None:
            raise ValueError("请先构建知识库")
        candidates = self.retrieval.hybrid_search(question)
        ranked = self._rerank(question, candidates)[: self.config.top_k]
        parents = self.data.parent_sections(ranked)
        if not parents:
            return NO_EVIDENCE
        if self.generation is None:
            # 选型记录三把千问温度定在 0，配置里的 0.1 不用于这条短路径。
            self.generation = GenerationIntegrationModule(
                EXPERIMENT_TEMPERATURE,
                self.config.max_tokens,
            )
        return _prefix_answer(self.generation.answer(question, parents, history))

    def _rerank(self, question: str, candidates: list[RagPiece]) -> list[RagPiece]:
        if self.reranker is None:
            self.reranker = load_text_reranker()
        return self.reranker.rerank(question, candidates)


def _selected_rag() -> ElderBrainRag:
    """命令行走 embedding-3 索引。默认目录仍是另一套嵌入，维度对不上。"""
    from modules.api_embeddings import load_embedding3
    from run_embedding3 import INDEX_DIR

    embedder = load_embedding3()
    rag = ElderBrainRag(RagConfig(index_dir=INDEX_DIR))
    rag.index.embeddings = embedder
    return rag


def _chunk_sha256(path: Path) -> str:
    digest = file_sha256(path)
    if digest is None:
        raise FileNotFoundError(f"块清单不存在: {path}")
    return digest


def _prefix_answer(body: str) -> str:
    # 拒答和证据不足已经带版本句。这里只给模型正文补开头和空行。
    return f"{VERSION_SENTENCE}\n\n{body}"


def _print_refusal(question: str) -> bool:
    """命中时直接打印拒答。调用方就此返回，不建库、不嵌入。"""
    if not question:
        return False
    refused = refusal_for(question)
    if refused is None:
        return False
    print(refused)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="用中文 SRD 5.1 块回答一个问题")
    parser.add_argument("question", nargs="?", default="")
    args = parser.parse_args()
    if _print_refusal(args.question):
        return
    rag = _selected_rag()
    count = rag.build_knowledge_base()
    print(f"已装入 {count} 个检索块")
    if not args.question:
        return
    print(rag.ask(args.question))


if __name__ == "__main__":
    main()
