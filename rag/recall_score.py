"""题目级召回：前 k 条里要包含每一条权威 entry_id。"""

from recall_questions import RecallQuestion

RECALL_AT_5 = 5
RECALL_AT_8 = 8


def is_hit(entry_ids: tuple[str, ...], ranked: list[str], cutoff: int) -> bool:
    """多证据题缺一条即未召回。cutoff 是前几条。"""
    if cutoff < 1:
        raise ValueError("召回条数必须为正")
    window = set(ranked[:cutoff])
    return all(entry_id in window for entry_id in entry_ids)


def first_rank(entry_ids: tuple[str, ...], ranked: list[str]) -> int | None:
    """全部权威条目都出现时，返回最靠后那条的名次。名次从 1 计。"""
    positions: list[int] = []
    for entry_id in entry_ids:
        if entry_id not in ranked:
            return None
        positions.append(ranked.index(entry_id) + 1)
    return max(positions)


def rate(questions: list[RecallQuestion], hits: list[bool]) -> float:
    """没有题时返回 0，避免用空分母假装满分。"""
    if len(questions) != len(hits):
        raise ValueError("题目和命中条数不一致")
    if not questions:
        return 0.0
    return sum(1 for hit in hits if hit) / len(questions)
