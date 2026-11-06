import unittest

from embed_latency import P50, P95, percentile_linear, query_cost_yuan


class PercentileTests(unittest.TestCase):
    def test_median_interpolates_an_even_count(self) -> None:
        self.assertEqual(percentile_linear([1, 2, 3, 4], P50), 2.5)

    def test_p95_sits_between_the_last_two(self) -> None:
        samples = [float(index) for index in range(1, 13)]
        self.assertAlmostEqual(percentile_linear(samples, P95), 11.45)

    def test_empty_samples_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            percentile_linear([], P50)


class QueryCostTests(unittest.TestCase):
    def test_cost_uses_the_per_million_price(self) -> None:
        self.assertAlmostEqual(query_cost_yuan(1000, 0.5), 0.0005)

    def test_negative_tokens_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            query_cost_yuan(-1, 0.5)


if __name__ == "__main__":
    unittest.main()
