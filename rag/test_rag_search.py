"""rag_search 在一轮内钉住已发布版本，并只交出父节字段。"""

import sys
from dataclasses import FrozenInstanceError, asdict, fields
from pathlib import Path

import pytest

_RAG_DIR = Path(__file__).resolve().parent
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

from modules.data_preparation import RagPiece
from modules.retrieval import RRF_K
from rag_search import ParentHit, pin_published_index, rag_search

VERSION_V1 = "v1"
VERSION_V2 = "v2"
ENTRY_V1 = "entry-fireball-v1"
ENTRY_V2 = "entry-fireball-v2"
ENTRY_CHILD = "entry-fireball-child"
PATH_FIREBALL = "Spells > Fireball"
TITLE_FIREBALL = "Fireball"
TITLE_FROM_META = "火球术"
BODY_V1 = "v1 火球术射程一百二十尺"
BODY_V2 = "v2 火球术射程六十尺"
BODY_CHILD = "子块不该出现在结果里"
BODY_WITHOUT_TITLE = "正文里的火球术不应当被摘成标题"
MODEL_QUERY = "fireball range"
QUESTION = "火球术的射程是多少"
BLANK_QUERY = " \n\t"
DOC_PARENT = "parent"
DOC_CHILD = "child"
HIT_FIELDS = ("entry_id", "title", "text", "score")
SCORE_EXPLICIT = "0.900000"
SCORE_UNPARSED = "高"


def _piece(
    entry_id: str,
    text: str,
    doc_type: str = DOC_PARENT,
    entry_path: str | None = PATH_FIREBALL,
    title: str | None = None,
    relevance_score: str | None = None,
) -> RagPiece:
    """拼一块测试用父节或子块。metadata 里故意带上不该交出去的字段。"""
    metadata = {
        "entry_id": entry_id,
        "doc_type": doc_type,
        "location": "PHB 241",
        "rule_version": "5.1",
    }
    if entry_path is not None:
        metadata["entry_path"] = entry_path
    if title is not None:
        metadata["title"] = title
    if relevance_score is not None:
        metadata["relevance_score"] = relevance_score
    return RagPiece(text=text, metadata=metadata)


class _LiveIndex:
    """假索引。read 返回活指针，search 按传入版本取块。"""

    def __init__(self, pieces_by_version: dict[str, tuple[RagPiece, ...]]) -> None:
        self._pieces = pieces_by_version
        self.version = VERSION_V1
        self.reads = 0
        self.calls: list[tuple[str, str]] = []

    def read(self) -> str:
        self.reads += 1
        return self.version

    def search(self, query: str, index_version: str) -> tuple[RagPiece, ...]:
        self.calls.append((query, index_version))
        return self._pieces.get(index_version, ())


def test_continue_search_keeps_the_pinned_version() -> None:
    """先钉住 v1，换掉活指针后再检索，两次仍打到 v1 的父节。"""
    index = _LiveIndex({
        VERSION_V1: (_piece(ENTRY_V1, BODY_V1),),
        VERSION_V2: (_piece(ENTRY_V2, BODY_V2),),
    })
    pinned = pin_published_index(index.read)
    first = rag_search(pinned, index.search, MODEL_QUERY, QUESTION)
    index.version = VERSION_V2
    second = rag_search(pinned, index.search, MODEL_QUERY, QUESTION)
    expected_calls = [(MODEL_QUERY, VERSION_V1), (MODEL_QUERY, VERSION_V1)]
    assert index.reads == 1
    assert index.calls == expected_calls
    assert [hit.entry_id for hit in first] == [ENTRY_V1]
    assert [hit.entry_id for hit in second] == [ENTRY_V1]


def test_hit_exposes_only_entry_title_text_and_score() -> None:
    """返回字段只有条目、标题、正文和分数。路径最后一段是标题。"""
    index = _LiveIndex({VERSION_V1: (_piece(ENTRY_V1, BODY_V1),)})
    pinned = pin_published_index(lambda: VERSION_V1)
    hits = rag_search(pinned, index.search, MODEL_QUERY, QUESTION)
    assert len(hits) == 1
    hit = hits[0]
    assert isinstance(hit, ParentHit)
    assert tuple(item.name for item in fields(hit)) == HIT_FIELDS
    assert set(asdict(hit)) == set(HIT_FIELDS)
    assert hit.entry_id == ENTRY_V1
    assert hit.title == TITLE_FIREBALL
    assert hit.text == BODY_V1
    assert hit.score == 1 / (RRF_K + 0)


def test_blank_model_query_uses_the_question() -> None:
    """模型查询词去掉空白后为空时，检索函数收到当前问题。"""
    index = _LiveIndex({VERSION_V1: (_piece(ENTRY_V1, BODY_V1),)})
    pinned = pin_published_index(lambda: VERSION_V1)
    rag_search(pinned, index.search, BLANK_QUERY, QUESTION)
    assert index.calls == [(QUESTION, VERSION_V1)]


def test_child_chunk_is_dropped() -> None:
    """doc_type 为 child 的块不作为父节返回。"""
    pieces = (
        _piece(ENTRY_CHILD, BODY_CHILD, doc_type=DOC_CHILD),
        _piece(ENTRY_V1, BODY_V1),
    )
    index = _LiveIndex({VERSION_V1: pieces})
    pinned = pin_published_index(lambda: VERSION_V1)
    hits = rag_search(pinned, index.search, MODEL_QUERY, QUESTION)
    assert [hit.entry_id for hit in hits] == [ENTRY_V1]
    assert BODY_CHILD not in [hit.text for hit in hits]


def test_blank_query_and_question_skip_retrieval() -> None:
    """查询词和当前问题都是空白时不调用检索，返回空列表。"""
    index = _LiveIndex({VERSION_V1: (_piece(ENTRY_V1, BODY_V1),)})
    pinned = pin_published_index(lambda: VERSION_V1)
    assert rag_search(pinned, index.search, BLANK_QUERY, " \t") == []
    assert index.calls == []


def test_unpublished_version_returns_empty() -> None:
    """还没有发布版本时钉住空版本，检索返回空列表且不抛异常。"""
    index = _LiveIndex({VERSION_V1: (_piece(ENTRY_V1, BODY_V1),)})
    pinned = pin_published_index(lambda: None)
    assert pinned.index_version == ""
    assert rag_search(pinned, index.search, MODEL_QUERY, QUESTION) == []
    assert index.calls == []


def test_metadata_title_and_relevance_score_are_used() -> None:
    """有 title 和可解析分数时用它们；解析不了则按父节顺序给分。"""
    pieces = (
        _piece(
            ENTRY_V1,
            BODY_V1,
            title=TITLE_FROM_META,
            relevance_score=SCORE_EXPLICIT,
        ),
        _piece(ENTRY_V2, BODY_V2, relevance_score=SCORE_UNPARSED),
    )
    index = _LiveIndex({VERSION_V1: pieces})
    pinned = pin_published_index(lambda: VERSION_V1)
    hits = rag_search(pinned, index.search, MODEL_QUERY, QUESTION)
    assert hits[0].title == TITLE_FROM_META
    assert hits[0].score == float(SCORE_EXPLICIT)
    assert hits[1].title == TITLE_FIREBALL
    assert hits[1].score == 1 / (RRF_K + 1)


def test_title_stays_blank_without_title_or_path() -> None:
    """没有 title 和 entry_path 时标题是空字符串，不从正文摘。"""
    piece = _piece(ENTRY_V1, BODY_WITHOUT_TITLE, entry_path=None)
    index = _LiveIndex({VERSION_V1: (piece,)})
    pinned = pin_published_index(lambda: VERSION_V1)
    hits = rag_search(pinned, index.search, MODEL_QUERY, QUESTION)
    assert hits[0].title == ""
    assert hits[0].text == BODY_WITHOUT_TITLE


def test_pinned_index_is_frozen() -> None:
    """钉住对象不可改，避免一轮中途把版本写回去。"""
    pinned = pin_published_index(lambda: VERSION_V1)
    with pytest.raises(FrozenInstanceError):
        setattr(pinned, "index_version", VERSION_V2)
