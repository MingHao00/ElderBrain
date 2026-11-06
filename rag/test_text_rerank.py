import json
import unittest
import urllib.error
from collections.abc import Callable
from typing import Any

from modules.data_preparation import RagPiece
from modules.text_rerank import TextReranker, _loads_rerank_body, _rerank_endpoint

Poster = Callable[[str, str, dict[str, Any]], dict[str, Any]]

SECRET = "super-secret-key"
ENDPOINT = "https://example.invalid/rerank"


class RerankEndpointTests(unittest.TestCase):
    def test_replaces_the_embedding_suffix(self) -> None:
        endpoint = _rerank_endpoint(
            "https://example.invalid/compatible-mode/v1"
        )
        self.assertEqual(
            endpoint,
            "https://example.invalid/api/v1/services/rerank/text-rerank/text-rerank",
        )

    def test_rejects_a_base_url_without_the_suffix(self) -> None:
        with self.assertRaises(ValueError):
            _rerank_endpoint("https://example.invalid/v1")


class TextRerankerTests(unittest.TestCase):
    def test_orders_by_the_returned_indexes(self) -> None:
        pieces = [
            RagPiece("甲", {"section_id": "a"}),
            RagPiece("乙", {"section_id": "b"}),
        ]
        calls: list[dict[str, object]] = []

        def poster(url: str, api_key: str, payload: dict[str, object]) -> dict[str, object]:
            calls.append(payload)
            self.assertEqual(url, "https://example.invalid/rerank")
            self.assertEqual(api_key, "test-key")
            return {
                "output": {
                    "results": [
                        {"index": 1, "relevance_score": 0.9},
                        {"index": 0, "relevance_score": 0.2},
                    ]
                }
            }

        reranker = TextReranker("https://example.invalid/rerank", "test-key", "qwen", poster)
        ranked = reranker.rerank("火球术射程", pieces)
        self.assertEqual([piece.text for piece in ranked], ["乙", "甲"])
        self.assertEqual(ranked[0].metadata["relevance_score"], "0.900000")
        self.assertEqual(calls[0]["model"], "qwen")

    def test_empty_candidates_do_not_call_the_api(self) -> None:
        def poster(url: str, api_key: str, payload: dict[str, object]) -> dict[str, object]:
            raise AssertionError("空候选不应请求重排")

        reranker = TextReranker("https://example.invalid/rerank", "test-key", "qwen", poster)
        self.assertEqual(reranker.rerank("火球术射程", []), [])

    def test_connection_timeout_and_non_json_become_value_error(self) -> None:
        failures: list[BaseException] = [
            urllib.error.URLError("connection failed"),
            TimeoutError("timed out"),
            json.JSONDecodeError("Expecting value", "not-json", 0),
        ]
        for failure in failures:
            reranker = _secret_reranker(_raise_poster(failure))
            with self.assertRaises(ValueError) as caught:
                reranker.rerank("火球术", [RagPiece("甲", {"section_id": "a"})])
            self.assertNotIn(SECRET, str(caught.exception))

    def test_missing_fields_and_non_numeric_score(self) -> None:
        payloads = [
            {"output": {"results": [{"relevance_score": 0.2}]}},
            {"output": {"results": [{"index": 0}]}},
            {"output": {"results": [{"index": 0, "relevance_score": "高"}]}},
        ]
        for payload in payloads:
            reranker = _secret_reranker(_payload_poster(payload))
            with self.assertRaises(ValueError) as caught:
                reranker.rerank("火球术", [RagPiece("甲", {"section_id": "a"})])
            self.assertNotIn(SECRET, str(caught.exception))

    def test_empty_and_non_json_bodies(self) -> None:
        with self.assertRaises(ValueError) as empty:
            _loads_rerank_body("  \n")
        self.assertNotIn(SECRET, str(empty.exception))
        with self.assertRaises(ValueError) as broken:
            _loads_rerank_body("not-json")
        self.assertNotIn(SECRET, str(broken.exception))


def _secret_reranker(poster: Poster) -> TextReranker:
    return TextReranker(ENDPOINT, SECRET, "qwen", poster)


def _raise_poster(failure: BaseException) -> Poster:
    def poster(url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        if api_key != SECRET:
            raise AssertionError("测试密钥被改写")
        raise failure

    return poster


def _payload_poster(body: dict[str, Any]) -> Poster:
    def poster(url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        if api_key != SECRET:
            raise AssertionError("测试密钥被改写")
        return body

    return poster


if __name__ == "__main__":
    unittest.main()
