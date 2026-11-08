"""ElderBrain 的 RAG 主体。四段流程来自 All-in-RAG 第 8 章。"""

import argparse
import sys
from pathlib import Path

_RAG_DIR = Path(__file__).resolve().parent
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

from dotenv import load_dotenv

from audit_record import (
    CITATION_FAILED,
    CITATION_PASSED,
    CITATION_PASSED_AFTER_RETRY,
    CITATION_UNCHECKED,
)
from citation_check import (
    ATTEMPT_FIRST,
    ATTEMPT_LAST,
    OUTCOME_PASS,
    OUTCOME_RETRY,
    CitedSentence,
    RetrievedParent,
    citation_attempt_outcome,
    citations_match,
)
from config import REPO_ROOT, RULE_VERSION, RagConfig
from generation_call import EXPERIMENT_TEMPERATURE
from generation_score import parse_generation
from modules.data_preparation import DataPreparationModule, RagPiece
from modules.generation import MISSING_TOKEN_COUNT, NO_EVIDENCE, GenerationIntegrationModule
from modules.index_construction import IndexConstructionModule, file_sha256
from modules.retrieval import RetrievalOptimizationModule
from modules.text_rerank import TextReranker, load_text_reranker
from scope import INJECTION_REFUSAL, VERSION_SENTENCE, refusal_for
from session_card import (
    CATEGORY_INJECTION,
    CATEGORY_NO_EVIDENCE,
    CATEGORY_OUT_OF_SCOPE,
    CardEntry,
    SessionCard,
    empty_card,
    entries_for_citations,
    retrieval_query,
)

load_dotenv(REPO_ROOT / ".env")


class CitedReply(str):
    """一次提问的页面文本。校验结果和各次生成用量跟这次文本走。"""

    citation_check: str
    usages: tuple[tuple[int, int], ...]

    def __new__(
        cls,
        text: str,
        citation_check: str,
        usages: tuple[tuple[int, int], ...] = (),
    ) -> "CitedReply":
        reply = str.__new__(cls, text)
        reply.citation_check = citation_check
        reply.usages = usages
        reply.sentences = ()
        reply.citations = ()
        reply.refusal_category = None
        reply.card_update = None
        return reply


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

    def ask(self, question: str, card: SessionCard | None = None) -> str:
        """版本和提示注入都在检索之前返回。检索带上卡片里的标题。

        生成之后做整段引用校验。第一次对不上，用同一批父节再生成一次。
        客户端历史不从这里进入。
        """
        active = card or empty_card()
        refused = refusal_for(question)
        if refused is not None:
            return _unchecked(refused, _scope_category(refused))
        if self.retrieval is None:
            raise ValueError("请先构建知识库")
        query = retrieval_query(question, active)
        candidates = self.retrieval.hybrid_search(query)
        ranked = self._rerank(query, candidates)[: self.config.top_k]
        parents = self.data.parent_sections(ranked)
        if not parents:
            return _unchecked(NO_EVIDENCE, CATEGORY_NO_EVIDENCE)
        return self._checked_generation(question, parents, active)

    def _generation(self) -> GenerationIntegrationModule:
        module = self.generation
        if module is not None:
            return module
        # 选型记录三把千问温度定在 0，配置里的 0.1 不用于这条短路径。
        module = GenerationIntegrationModule(
            EXPERIMENT_TEMPERATURE,
            self.config.max_tokens,
        )
        self.generation = module
        return module

    def _checked_generation(
        self,
        question: str,
        parents: list[RagPiece],
        card: SessionCard,
    ) -> str:
        module = self._generation()
        retrieved = _retrieved_parents(parents)
        first_text, first_use = _generate(module, question, parents, card)
        judged = _judged(first_text, retrieved, ATTEMPT_FIRST, (first_use,), parents)
        if judged is not None:
            return judged
        second_text, second_use = _generate(module, question, parents, card)
        return _finished(
            _judged(second_text, retrieved, ATTEMPT_LAST, (first_use, second_use), parents),
        )

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


def _unchecked(text: str, category: str) -> CitedReply:
    reply = CitedReply(text, CITATION_UNCHECKED)
    reply.sentences = (CitedSentence(_after_version(text), None),)
    reply.refusal_category = category
    return reply


def _scope_category(text: str) -> str:
    if text == INJECTION_REFUSAL:
        return CATEGORY_INJECTION
    return CATEGORY_OUT_OF_SCOPE


def _generate(
    module: GenerationIntegrationModule,
    question: str,
    parents: list[RagPiece],
    card: SessionCard,
) -> tuple[str, tuple[int, int]]:
    body = module.answer(question, parents, card)
    return body, _token_pair(body)


def _token_pair(body: str) -> tuple[int, int]:
    return (
        _token_count(getattr(body, "prompt_tokens", MISSING_TOKEN_COUNT)),
        _token_count(getattr(body, "completion_tokens", MISSING_TOKEN_COUNT)),
    )


def _token_count(value: object) -> int:
    # 缺字段或类型不对时记 0。不能编造，也不能因此让这次提问失败。
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return MISSING_TOKEN_COUNT
    return value


def _judged(
    body: str,
    retrieved: list[RetrievedParent] | None,
    attempt: int,
    usages: tuple[tuple[int, int], ...],
    parents: list[RagPiece],
) -> str | None:
    outcome = citation_attempt_outcome(_body_passes(body, retrieved), attempt)
    if outcome == OUTCOME_RETRY:
        return None
    if outcome == OUTCOME_PASS:
        return _passed_reply(body, attempt, usages, parents)
    return _no_evidence(usages)


def _finished(reply: str | None) -> str:
    # 第二次不会再要求重试。断言只防止校验函数以后改出口径。
    assert reply is not None
    return reply


def _passed_reply(
    body: str,
    attempt: int,
    usages: tuple[tuple[int, int], ...],
    parents: list[RagPiece],
) -> CitedReply:
    check = CITATION_PASSED_AFTER_RETRY
    if attempt == ATTEMPT_FIRST:
        check = CITATION_PASSED
    reply = CitedReply(_prefix_answer(body), check, usages)
    sentences = _cited_sentences(body) or []
    reply.sentences = tuple(sentences)
    reply.citations = _citations(sentences, parents)
    reply.card_update = reply.citations or None
    return reply


def _no_evidence(usages: tuple[tuple[int, int], ...]) -> CitedReply:
    # 两次都对不上时不保留模型原句，卡片也不要写成这些句子。
    reply = CitedReply(NO_EVIDENCE, CITATION_FAILED, usages)
    reply.sentences = (CitedSentence(_after_version(NO_EVIDENCE), None),)
    reply.refusal_category = CATEGORY_NO_EVIDENCE
    return reply


def _citations(
    sentences: list[CitedSentence],
    parents: list[RagPiece],
) -> tuple[CardEntry, ...]:
    metadata: dict[str, dict[str, str]] = {}
    for parent in parents:
        entry_id = parent.metadata.get("entry_id", "")
        if entry_id and entry_id not in metadata:
            metadata[entry_id] = parent.metadata
    cited = [item.entry_id for item in sentences if item.entry_id]
    return entries_for_citations(cited, metadata)


def _after_version(text: str) -> str:
    if text.startswith(VERSION_SENTENCE):
        rest = text[len(VERSION_SENTENCE) :].strip()
        if rest:
            return rest
    return text.strip()


def _body_passes(body: str, retrieved: list[RetrievedParent] | None) -> bool:
    # 解析不成句子就不能当成通过。模型不写版本句，版本用规则库常量和条目比。
    sentences = _cited_sentences(body)
    # 证据不足时的合法输出是空数组。citations_match 对空列表返回 True，必须在调用前判失败。
    if not sentences or retrieved is None:
        return False
    try:
        return citations_match(sentences, retrieved, RULE_VERSION)
    except ValueError:
        # 格式不被校验函数接受时，按这次失败走重试，不要把提问打成 500。
        return False


def _cited_sentences(body: str) -> list[CitedSentence] | None:
    payload = parse_generation(body)
    if payload is None:
        return None
    raw = payload.get("sentences")
    if not isinstance(raw, list):
        return None
    return _sentence_list(raw)


def _sentence_list(raw: list[object]) -> list[CitedSentence] | None:
    sentences: list[CitedSentence] = []
    for item in raw:
        sentence = _one_sentence(item)
        if sentence is None:
            return None
        sentences.append(sentence)
    return sentences


def _one_sentence(item: object) -> CitedSentence | None:
    if not isinstance(item, dict):
        return None
    text = item.get("text")
    entry_id = item.get("entry_id")
    if not isinstance(text, str) or not text.strip():
        return None
    if entry_id is None:
        return CitedSentence(text, None)
    if isinstance(entry_id, str) and entry_id.strip():
        return CitedSentence(text, entry_id)
    return None


def _retrieved_parents(parents: list[RagPiece]) -> list[RetrievedParent] | None:
    retrieved: list[RetrievedParent] = []
    for parent in parents:
        item = _one_parent(parent)
        if item is None:
            return None
        retrieved.append(item)
    return retrieved


def _one_parent(parent: RagPiece) -> RetrievedParent | None:
    entry_id = parent.metadata.get("entry_id", "")
    rule_version = parent.metadata.get("rule_version", "")
    if not entry_id.strip() or not rule_version.strip():
        return None
    return RetrievedParent(entry_id, parent.text, rule_version)


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
