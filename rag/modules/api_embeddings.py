"""调用 OpenAI 兼容的嵌入接口。向量在接口侧计算，本机不加载模型。"""

import math
from typing import Protocol

from langchain_core.embeddings import Embeddings
from openai import OpenAI

from config import require_env

EMBEDDING_BATCH_SIZE = 10
MIN_DIMENSION = 1
EMBEDDING_API_KEY = "EMBEDDING_API_KEY"
EMBEDDING_BASE_URL = "EMBEDDING_BASE_URL"
EMBEDDING_MODEL = "EMBEDDING_MODEL"
COMPARE_EMBEDDING_API_KEY = "COMPARE_EMBEDDING_API_KEY"
COMPARE_EMBEDDING_BASE_URL = "COMPARE_EMBEDDING_BASE_URL"
COMPARE_EMBEDDING_MODEL = "COMPARE_EMBEDDING_MODEL"
EMBEDDING3_MODEL_NAME = "embedding-3"
# 官方默认维度。高精度检索用这一档，不缩短到与千问相同的 1024。
EMBEDDING3_DIMENSIONS = 2048
EMBEDDING3_PATH_SUFFIX = "/paas/v4"
EMBEDDING3_CALL_SUFFIX = "/embeddings"


class _EmbeddingItem(Protocol):
    index: int
    embedding: list[float]


class _EmbeddingResponse(Protocol):
    data: list[_EmbeddingItem]


class _EmbeddingCalls(Protocol):
    def create(
        self,
        *,
        model: str,
        input: list[str],
        dimensions: int | None = None,
    ) -> _EmbeddingResponse:
        """提交一批文本，返回同序向量。dimensions 为空时不传。"""


class EmbeddingClient(Protocol):
    """只使用嵌入这一路，避免测试依赖完整的 OpenAI 客户端。"""

    embeddings: _EmbeddingCalls


class ApiEmbeddings(Embeddings):
    """FAISS 查询和建索引都走这个外部接口。"""

    def __init__(
        self,
        client: EmbeddingClient,
        model_name: str,
        dimensions: int | None = None,
    ) -> None:
        """指定维度时随请求发送。不指定则使用接口自己的默认值。"""
        if dimensions is not None and dimensions < MIN_DIMENSION:
            raise ValueError("嵌入维度必须为正整数")
        self.client = client
        self.model_name = model_name
        self.dimensions = dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """按批调用接口。空列表不发请求。"""
        if not texts:
            return []
        _check_texts(texts)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = texts[start:start + EMBEDDING_BATCH_SIZE]
            vectors.extend(_embed_batch(
                self.client,
                self.model_name,
                batch,
                self.dimensions,
            ))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        """单条查询与建索引使用同一模型和同一归一化。"""
        return self.embed_documents([text])[0]


def load_api_embeddings() -> ApiEmbeddings:
    """从环境变量创建嵌入客户端。缺任何一项都直接报错。"""
    model_name = require_env(EMBEDDING_MODEL)
    client = OpenAI(
        api_key=require_env(EMBEDDING_API_KEY),
        base_url=require_env(EMBEDDING_BASE_URL),
    )
    return ApiEmbeddings(client, model_name)


def load_embedding3() -> ApiEmbeddings:
    """智谱 embedding-3。密钥与千问分开，重排仍走原来的嵌入配置。"""
    model_name = require_env(COMPARE_EMBEDDING_MODEL)
    if model_name != EMBEDDING3_MODEL_NAME:
        raise ValueError("本轮对照模型必须是 embedding-3")
    client = OpenAI(
        api_key=require_env(COMPARE_EMBEDDING_API_KEY),
        base_url=_embedding3_base_url(require_env(COMPARE_EMBEDDING_BASE_URL)),
    )
    return ApiEmbeddings(client, model_name, EMBEDDING3_DIMENSIONS)


def _embedding3_base_url(raw: str) -> str:
    # 文档写的是完整路径。OpenAI 客户端会自己再加 /embeddings，这里先去掉。
    base = raw.strip().rstrip("/")
    if base.endswith(EMBEDDING3_CALL_SUFFIX):
        base = base[: -len(EMBEDDING3_CALL_SUFFIX)].rstrip("/")
    if not base.endswith(EMBEDDING3_PATH_SUFFIX):
        raise ValueError("COMPARE_EMBEDDING_BASE_URL 必须以 /paas/v4 结尾")
    return base


def _check_texts(texts: list[str]) -> None:
    if any(not isinstance(text, str) or not text.strip() for text in texts):
        raise ValueError("嵌入文本必须是非空字符串")


def _embed_batch(
    client: EmbeddingClient,
    model_name: str,
    texts: list[str],
    dimensions: int | None,
) -> list[list[float]]:
    response = _create_embeddings(client, model_name, texts, dimensions)
    return _read_vectors(response, len(texts))


def _create_embeddings(
    client: EmbeddingClient,
    model_name: str,
    texts: list[str],
    dimensions: int | None,
) -> _EmbeddingResponse:
    # 千问这条路径不传维度，沿用接口默认的 1024。对照模型必须带上自己的维度。
    if dimensions is None:
        return client.embeddings.create(model=model_name, input=texts)
    return client.embeddings.create(
        model=model_name,
        input=texts,
        dimensions=dimensions,
    )


def _read_vectors(response: _EmbeddingResponse, expected: int) -> list[list[float]]:
    data = response.data
    if len(data) != expected:
        raise ValueError("嵌入接口返回的条数与输入不一致")
    ordered = sorted(data, key=lambda item: item.index)
    _check_indexes([item.index for item in ordered], expected)
    return [_as_unit_vector(item.embedding) for item in ordered]


def _check_indexes(indexes: list[int], expected: int) -> None:
    # 条数对了仍可能重复或跳号，排序后必须正好是 0..n-1。
    if indexes != list(range(expected)):
        raise ValueError("嵌入接口返回的序号不完整")


def _as_unit_vector(raw: list[float]) -> list[float]:
    # FAISS 用 L2。单位向量让距离和余弦一致，换接口后仍可比较。
    if not raw:
        raise ValueError("嵌入接口返回的向量无效")
    values = [float(value) for value in raw]
    if any(not math.isfinite(value) for value in values):
        raise ValueError("嵌入接口返回的向量含有非有限数值")
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        raise ValueError("嵌入接口返回了零向量")
    return [value / norm for value in values]
