"""调用百炼文本重排。密钥和主机与嵌入接口相同，路径不同。"""

import json
import math
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from config import require_env
from modules.data_preparation import RagPiece

RERANK_MODEL = "RERANK_MODEL"
RERANK_TIMEOUT_SECONDS = 30
SCORE_DIGITS = 6
EMBEDDING_SUFFIX = "/compatible-mode/v1"
RERANK_PATH = "/api/v1/services/rerank/text-rerank/text-rerank"

Poster = Callable[[str, str, dict[str, Any]], dict[str, Any]]


class TextReranker:
    """按接口返回的相关性重排候选块。"""

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        model_name: str,
        poster: Poster | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        self.model_name = model_name
        self.poster = poster if poster is not None else _post_json

    def rerank(self, query: str, pieces: list[RagPiece]) -> list[RagPiece]:
        """空候选不请求接口。结果顺序就是相关性从高到低。"""
        if not query.strip():
            raise ValueError("重排查询不能是空字符串")
        if not pieces:
            return []
        payload = {
            "model": self.model_name,
            "input": {"query": query, "documents": [piece.text for piece in pieces]},
        }
        response = _call_poster(self.poster, self.endpoint, self.api_key, payload)
        return _order_pieces(pieces, _results(response))


def load_text_reranker() -> TextReranker:
    """从环境变量创建重排客户端。模型名单独配置，密钥沿用嵌入。"""
    return TextReranker(
        _rerank_endpoint(require_env("EMBEDDING_BASE_URL")),
        require_env("EMBEDDING_API_KEY"),
        require_env(RERANK_MODEL),
    )


def _rerank_endpoint(embedding_base_url: str) -> str:
    base = embedding_base_url.strip().rstrip("/")
    if not base.endswith(EMBEDDING_SUFFIX):
        raise ValueError("EMBEDDING_BASE_URL 必须以 /compatible-mode/v1 结尾")
    host = base[: -len(EMBEDDING_SUFFIX)]
    return host + RERANK_PATH


def _results(response: dict[str, Any]) -> list[dict[str, Any]]:
    if response.get("code"):
        message = str(response.get("message") or response["code"])
        raise ValueError(f"重排接口失败: {message}")
    output = response.get("output")
    if not isinstance(output, dict):
        raise ValueError("重排接口没有返回排序结果")
    results = output.get("results")
    if not isinstance(results, list):
        raise ValueError("重排接口没有返回排序结果")
    return results


def _call_poster(
    poster: Poster,
    endpoint: str,
    api_key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    # 异常文本不带密钥。密钥只出现在请求头里。
    try:
        return poster(endpoint, api_key, payload)
    except TimeoutError as exc:
        raise ValueError("重排接口超时") from exc
    except urllib.error.URLError as exc:
        raise ValueError("重排接口连接失败") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("重排接口返回的内容不是 JSON") from exc


def _order_pieces(pieces: list[RagPiece], results: list[Any]) -> list[RagPiece]:
    ranked: list[RagPiece] = []
    seen: set[int] = set()
    for item in results:
        index, score = _hit(item)
        if index < 0 or index >= len(pieces) or index in seen:
            raise ValueError("重排接口返回的序号无效")
        seen.add(index)
        ranked.append(_with_score(pieces[index], score))
    if len(ranked) != len(pieces):
        raise ValueError("重排接口返回的条数与候选不一致")
    return ranked


def _hit(item: object) -> tuple[int, float]:
    if not isinstance(item, dict):
        raise ValueError("重排接口返回的结果缺少序号或分数")
    if "index" not in item or "relevance_score" not in item:
        raise ValueError("重排接口返回的结果缺少序号或分数")
    return _index_and_score(item["index"], item["relevance_score"])


def _index_and_score(index: object, score: object) -> tuple[int, float]:
    if isinstance(index, bool) or not isinstance(index, int):
        raise ValueError("重排接口返回的序号无效")
    if not _is_score(score):
        raise ValueError("重排接口返回的分数不是数字")
    return index, float(score)


def _is_score(score: object) -> bool:
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return False
    return math.isfinite(score)


def _with_score(piece: RagPiece, score: float) -> RagPiece:
    metadata = dict(piece.metadata)
    metadata["relevance_score"] = f"{score:.{SCORE_DIGITS}f}"
    return RagPiece(piece.text, metadata)


def _post_json(url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    return _loads_rerank_body(_read_http(request))


def _read_http(request: urllib.request.Request) -> str:
    try:
        with urllib.request.urlopen(request, timeout=RERANK_TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"重排接口失败: {exc.code} {_error_text(detail)}") from exc
    except TimeoutError as exc:
        raise ValueError("重排接口超时") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise ValueError("重排接口超时") from exc
        raise ValueError("重排接口连接失败") from exc


def _loads_rerank_body(raw: str) -> dict[str, Any]:
    if not raw.strip():
        raise ValueError("重排接口返回空正文")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("重排接口返回的内容不是 JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("重排接口返回的内容不是对象")
    return parsed


def _error_text(detail: str) -> str:
    try:
        payload = json.loads(detail)
    except json.JSONDecodeError:
        return "响应无法解析"
    if isinstance(payload, dict) and payload.get("message"):
        return str(payload["message"])
    return "响应无法解析"
