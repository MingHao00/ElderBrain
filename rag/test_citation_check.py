"""引用校验的纯判定。不调用模型。"""

import unittest

from citation_check import (
    ATTEMPT_FIRST,
    ATTEMPT_LAST,
    OUTCOME_PASS,
    OUTCOME_R2,
    OUTCOME_RETRY,
    CitedSentence,
    RetrievedParent,
    citation_attempt_outcome,
    citations_match,
)

FIREBALL = "srd51:spell:fireball"
MISSILE = "srd51:spell:magic-missile"
VERSION_51 = "5.1"
VERSION_52 = "5.2"
FIREBALL_TEXT = "射程 150 尺。造成 8d6 火焰伤害。"


def _parent(
    entry_id: str = FIREBALL,
    text: str = FIREBALL_TEXT,
    rule_version: str = VERSION_51,
) -> RetrievedParent:
    return RetrievedParent(entry_id, text, rule_version)


class CitationCheckTests(unittest.TestCase):
    def test_citation_in_retrieval_and_numbers_in_source_passes(self) -> None:
        # 条目编号里的 51 不是规则数值，原文没有单独的 51 也应通过。
        sentence = CitedSentence(
            "见 srd51:spell:fireball，射程 150 尺，造成 8d6 伤害。",
            FIREBALL,
        )
        matched = citations_match([sentence], [_parent()], VERSION_51)
        self.assertTrue(matched)
        self.assertEqual(
            citation_attempt_outcome(matched, ATTEMPT_FIRST),
            OUTCOME_PASS,
        )

    def test_citation_outside_retrieval_fails(self) -> None:
        sentence = CitedSentence("射程 150 尺。", FIREBALL)
        retrieved = [_parent(MISSILE, "射程 150 尺。")]
        self.assertFalse(citations_match([sentence], retrieved, VERSION_51))

    def test_dice_or_number_missing_from_its_parent_fails(self) -> None:
        dice = CitedSentence("造成 8d6 火焰伤害。", FIREBALL)
        wrong_dice = [_parent(text="造成 6d6 火焰伤害。")]
        self.assertFalse(citations_match([dice], wrong_dice, VERSION_51))
        # 150 尺只在另一父节里，不能算到这句所引的火球术上。
        number = CitedSentence("射程 150 尺。", FIREBALL)
        other_parent = [
            _parent(text="球形区域。"),
            _parent(MISSILE, "射程 150 尺。"),
        ]
        self.assertFalse(citations_match([number], other_parent, VERSION_51))

    def test_version_mismatch_with_rule_version_fails(self) -> None:
        sentence = CitedSentence("球形火焰。", FIREBALL)
        same_entry = [_parent(text="球形火焰。")]
        self.assertFalse(citations_match([sentence], same_entry, VERSION_52))
        # 编号以 srd51: 开头也不能顶替条目上的 rule_version。
        other_version = [_parent(text="球形火焰。", rule_version=VERSION_52)]
        self.assertFalse(citations_match([sentence], other_version, VERSION_51))

    def test_uncited_sentence_with_dice_or_distance_fails(self) -> None:
        dice = CitedSentence("造成 8d6 火焰伤害。", None)
        distance = CitedSentence("射程 150 尺。", None)
        self.assertFalse(citations_match([dice], [], VERSION_51))
        self.assertFalse(citations_match([distance], [], VERSION_51))

    def test_uncited_sentence_without_numbers_does_not_fail(self) -> None:
        question = CitedSentence("你指的是哪个法术？", None)
        self.assertTrue(citations_match([question], [], VERSION_52))
        cited = CitedSentence("射程 150 尺。", FIREBALL)
        matched = citations_match([question, cited], [_parent()], VERSION_51)
        self.assertTrue(matched)

    def test_first_failure_retries_and_second_failure_is_r2(self) -> None:
        failed = citations_match(
            [CitedSentence("见另一条。", FIREBALL)],
            [],
            VERSION_51,
        )
        self.assertFalse(failed)
        self.assertEqual(
            citation_attempt_outcome(failed, ATTEMPT_FIRST),
            OUTCOME_RETRY,
        )
        self.assertEqual(
            citation_attempt_outcome(failed, ATTEMPT_LAST),
            OUTCOME_R2,
        )
        passed = citations_match(
            [CitedSentence("射程 150 尺。", FIREBALL)],
            [_parent()],
            VERSION_51,
        )
        self.assertEqual(
            citation_attempt_outcome(passed, ATTEMPT_LAST),
            OUTCOME_PASS,
        )


if __name__ == "__main__":
    unittest.main()
