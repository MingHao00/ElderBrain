import unittest

from citation_bootstrap import QuestionSupport, interval, paired_diffs, support_diff


class CitationBootstrapTests(unittest.TestCase):
    def test_equal_rates_have_a_zero_difference(self) -> None:
        questions = [
            QuestionSupport("L06", 1, 1, 1, 1),
            QuestionSupport("M01", 2, 2, 1, 1),
        ]
        self.assertEqual(support_diff(questions), 0.0)

    def test_resample_keeps_a_question_together(self) -> None:
        questions = [QuestionSupport("M05", 1, 1, 0, 1)]
        diffs = paired_diffs(questions, draws=20, seed=42)
        self.assertEqual(diffs, [1.0] * 20)

    def test_interval_of_a_constant_difference_is_that_difference(self) -> None:
        low, high = interval([0.2] * 100)
        self.assertEqual(low, 0.2)
        self.assertEqual(high, 0.2)

    def test_missing_citations_cannot_make_a_rate(self) -> None:
        questions = [QuestionSupport("R09", 2, 3, 0, 0)]
        self.assertIsNone(support_diff(questions))


if __name__ == "__main__":
    unittest.main()
