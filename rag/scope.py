"""问到未收录版本或提示注入时直接拒答，不检索。"""

VERSION_SENTENCE = "按中文 SRD 5.1（2014 规则，机器翻译）作答。"

OUT_OF_SCOPE_TERMS = (
    "5.2",
    "5.2.1",
    "2024",
    "新版",
    "新规则",
    "5r",
    "5.5e",
    "one d&d",
    "对比",
    "区别",
    "差异",
    "新旧",
    "两版",
)

# 小写后做子串匹配。这里先写成小写，调用方不再各自折叠。
INJECTION_TERMS = (
    "忽略以上",
    "忽略之前",
    "忽略上述",
    "ignore previous",
    "ignore above",
    "ignore all instructions",
    "系统提示词",
    "system prompt",
    "越狱",
    "jailbreak",
)

REFUSAL = (
    VERSION_SENTENCE
    + "当前只收录中文 SRD 5.1，不能回答 2024 规则或两版对比。"
)

# 不写出命中了哪个词，避免把拦截规则交回去。
INJECTION_REFUSAL = VERSION_SENTENCE + "这个问题不能回答。"


def is_out_of_scope(question: str) -> bool:
    """问题要求 5.2 或两版对比时返回 True。"""
    return _contains_any(question, OUT_OF_SCOPE_TERMS)


def is_prompt_injection(question: str) -> bool:
    """问题试图改写指令或索要提示词时返回 True。"""
    return _contains_any(question, INJECTION_TERMS)


def refusal_for(question: str) -> str | None:
    """检索之前结束。版本拒答保持原句，并排在注入之前。"""
    if is_out_of_scope(question):
        return REFUSAL
    if is_prompt_injection(question):
        return INJECTION_REFUSAL
    return None


def _contains_any(question: str, terms: tuple[str, ...]) -> bool:
    # 英文关键词大小写不固定，先折叠再做子串匹配。
    folded = question.lower()
    return any(term in folded for term in terms)
