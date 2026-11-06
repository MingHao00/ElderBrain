"""第一期引用校验。只判定引用、数值和版本，不调用生成模型。"""

from dataclasses import dataclass

from generation_score import _claims_supported

OUTCOME_PASS = "通过"
OUTCOME_RETRY = "重试"
OUTCOME_R2 = "R2"
ATTEMPT_FIRST = 1
ATTEMPT_LAST = 2
_ALLOWED_ATTEMPTS = (ATTEMPT_FIRST, ATTEMPT_LAST)


@dataclass(frozen=True)
class CitedSentence:
    """一句回答。entry_id 为 None 表示这句不引用条目。"""

    text: str
    entry_id: str | None


@dataclass(frozen=True)
class RetrievedParent:
    """本次检索留下的父节，以及该条目自己的 rule_version。"""

    entry_id: str
    text: str
    rule_version: str


def citations_match(
    sentences: list[CitedSentence],
    retrieved: list[RetrievedParent],
    stated_version: str,
) -> bool:
    """判断本次回答的引用是否成立。

    sentences 是带 entry_id 的句子，retrieved 是本次检索到的父节。
    stated_version 是回答标注的版本，要与每个被引用条目的 rule_version 相同。
    全部成立返回 True。引用对不上，或无引用却写出骰子、数字时，返回 False。
    句子或检索结果格式不对时抛出 ValueError。
    """
    _require_version(stated_version)
    parents = _parents_by_id(retrieved)
    _require_sentences(sentences)
    return all(_sentence_matches(item, parents, stated_version) for item in sentences)


def citation_attempt_outcome(passed: bool, attempt: int) -> str:
    """把一次校验结果收成通过、重试或 R2。

    passed 为 True 时返回通过，第 1 次和第 2 次都一样。
    第 1 次失败返回重试，第 2 次仍失败返回 R2。不调用生成模型。
    passed 不是布尔值，或 attempt 超出这两次时，抛出 ValueError。
    """
    if type(passed) is not bool:
        raise ValueError("校验结果必须是布尔值")
    if type(attempt) is not int or attempt not in _ALLOWED_ATTEMPTS:
        raise ValueError("引用校验只判断第 1 次和第 2 次")
    if passed:
        return OUTCOME_PASS
    if attempt == ATTEMPT_FIRST:
        return OUTCOME_RETRY
    return OUTCOME_R2


def _sentence_matches(
    sentence: CitedSentence,
    parents: dict[str, RetrievedParent],
    stated_version: str,
) -> bool:
    # 无引用时没有父节。原函数见到骰子或数字且 entry_id 不是字符串就失败，没有则通过。
    if sentence.entry_id is None:
        return _claims_supported(sentence.text, None, {})
    parent = parents.get(sentence.entry_id)
    if parent is None:
        return False
    text = sentence.text
    entry_id = sentence.entry_id
    parent_text = parent.text
    # 只调用原函数，避免手写 compact 之后和 generation_score 分叉。
    if not _claims_supported(text, entry_id, {entry_id: parent_text}):
        return False
    # 编号前缀 srd51: 不是规则版本，版本只跟条目的 rule_version 比。
    return stated_version == parent.rule_version


def _require_sentences(sentences: list[CitedSentence]) -> None:
    if not isinstance(sentences, list):
        raise ValueError("句子必须是列表")
    for item in sentences:
        _require_sentence(item)


def _require_sentence(item: object) -> None:
    if not isinstance(item, CitedSentence):
        raise ValueError("句子格式无效")
    if not isinstance(item.text, str) or not item.text.strip():
        raise ValueError("句子格式无效")
    if item.entry_id is not None and not _nonempty_text(item.entry_id):
        raise ValueError("句子格式无效")


def _parents_by_id(retrieved: list[RetrievedParent]) -> dict[str, RetrievedParent]:
    if not isinstance(retrieved, list):
        raise ValueError("检索结果必须是列表")
    parents: dict[str, RetrievedParent] = {}
    for item in retrieved:
        _require_parent(item)
        parents[item.entry_id] = item
    return parents


def _require_parent(item: object) -> None:
    if not isinstance(item, RetrievedParent):
        raise ValueError("检索结果格式无效")
    if not _nonempty_text(item.entry_id) or not isinstance(item.text, str):
        raise ValueError("检索结果格式无效")
    if not _nonempty_text(item.rule_version):
        raise ValueError("检索结果格式无效")


def _require_version(stated_version: object) -> None:
    if not isinstance(stated_version, str):
        raise ValueError("标注版本必须是字符串")


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
