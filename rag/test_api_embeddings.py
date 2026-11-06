import json
import math
import os
import tempfile
import unittest
from pathlib import Path

from langchain_core.embeddings import Embeddings

from modules.api_embeddings import (
    COMPARE_EMBEDDING_API_KEY,
    COMPARE_EMBEDDING_BASE_URL,
    COMPARE_EMBEDDING_MODEL,
    EMBEDDING3_DIMENSIONS,
    EMBEDDING_API_KEY,
    EMBEDDING_BASE_URL,
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_MODEL,
    ApiEmbeddings,
    load_api_embeddings,
    load_embedding3,
)
from modules.data_preparation import RagPiece
from modules.generation import NO_EVIDENCE, GenerationIntegrationModule
from modules.index_construction import (
    MANIFEST_CHUNK_SHA256,
    MANIFEST_NAME,
    IndexConstructionModule,
)
from scope import VERSION_SENTENCE

ENV_NAMES = (EMBEDDING_API_KEY, EMBEDDING_BASE_URL, EMBEDDING_MODEL)


class _Item:
    def __init__(self, index: int, embedding: list[float]) -> None:
        self.index = index
        self.embedding = embedding


class _Response:
    def __init__(self, items: list[_Item]) -> None:
        self.data = items


class _Calls:
    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def create(self, *, model: str, input: list[str]) -> _Response:
        self.batches.append(list(input))
        items = [_Item(index, [3.0, 4.0]) for index, _text in enumerate(input)]
        return _Response(list(reversed(items)))


class _Client:
    def __init__(self) -> None:
        self.embeddings = _Calls()


class _DimensionCalls:
    def __init__(self) -> None:
        self.dimensions: int | None = None

    def create(
        self,
        *,
        model: str,
        input: list[str],
        dimensions: int | None = None,
    ) -> _Response:
        self.dimensions = dimensions
        return _Response([_Item(0, [3.0, 4.0])])


class _DimensionClient:
    def __init__(self, calls: _DimensionCalls) -> None:
        self.embeddings = calls


FAKE_EMBEDDING_MODEL = "fake-model"
SHA256_HEX_CHARS = 64
CHUNK_SHA256 = "a" * SHA256_HEX_CHARS


class _FixedEmbeddings(Embeddings):
    model_name = FAKE_EMBEDDING_MODEL

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _text in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0]


class _ScriptedCalls:
    def __init__(self, items: list[_Item]) -> None:
        self.items = items

    def create(self, *, model: str, input: list[str]) -> _Response:
        return _Response(self.items)


class _ScriptedClient:
    def __init__(self, items: list[_Item]) -> None:
        self.embeddings = _ScriptedCalls(items)


class ApiEmbeddingTests(unittest.TestCase):
    def test_batches_and_normalizes_in_request_order(self) -> None:
        client = _Client()
        embedder = ApiEmbeddings(client, "test-model")
        texts = [f"块{index}" for index in range(EMBEDDING_BATCH_SIZE + 1)]
        vectors = embedder.embed_documents(texts)
        self.assertEqual(len(client.embeddings.batches), 2)
        self.assertEqual(len(client.embeddings.batches[0]), EMBEDDING_BATCH_SIZE)
        self.assertEqual(vectors[0], [0.6, 0.8])
        self.assertEqual(embedder.embed_query("火球术"), [0.6, 0.8])

    def test_duplicate_index_is_rejected(self) -> None:
        embedder = ApiEmbeddings(
            _ScriptedClient([_Item(0, [3.0, 4.0]), _Item(0, [3.0, 4.0])]),
            "test-model",
        )
        with self.assertRaises(ValueError):
            embedder.embed_documents(["甲", "乙"])

    def test_missing_index_is_rejected(self) -> None:
        embedder = ApiEmbeddings(
            _ScriptedClient([_Item(0, [3.0, 4.0]), _Item(2, [3.0, 4.0])]),
            "test-model",
        )
        with self.assertRaises(ValueError):
            embedder.embed_documents(["甲", "乙"])

    def test_nan_vector_is_rejected(self) -> None:
        embedder = ApiEmbeddings(
            _ScriptedClient([_Item(0, [math.nan, 1.0])]),
            "test-model",
        )
        with self.assertRaises(ValueError):
            embedder.embed_documents(["甲"])

    def test_explicit_dimensions_are_sent(self) -> None:
        calls = _DimensionCalls()
        embedder = ApiEmbeddings(_DimensionClient(calls), "embedding-3", EMBEDDING3_DIMENSIONS)
        embedder.embed_documents(["甲"])
        self.assertEqual(calls.dimensions, EMBEDDING3_DIMENSIONS)

    def test_embedding3_uses_the_default_dimension(self) -> None:
        saved = _set_compare_env("embedding-3", "https://open.bigmodel.cn/api/paas/v4/")
        try:
            embedder = load_embedding3()
            self.assertEqual(embedder.model_name, "embedding-3")
            self.assertEqual(embedder.dimensions, EMBEDDING3_DIMENSIONS)
        finally:
            _restore_env(saved)

    def test_embedding3_rejects_a_different_model(self) -> None:
        saved = _set_compare_env("embedding-2", "https://open.bigmodel.cn/api/paas/v4")
        try:
            with self.assertRaises(ValueError):
                load_embedding3()
        finally:
            _restore_env(saved)

    def test_embedding3_accepts_the_full_embeddings_path(self) -> None:
        saved = _set_compare_env(
            "embedding-3",
            "https://open.bigmodel.cn/api/paas/v4/embeddings",
        )
        try:
            embedder = load_embedding3()
            self.assertEqual(embedder.dimensions, EMBEDDING3_DIMENSIONS)
        finally:
            _restore_env(saved)

    def test_missing_settings_raise(self) -> None:
        saved = {name: os.environ.get(name) for name in ENV_NAMES}
        try:
            for name in ENV_NAMES:
                os.environ.pop(name, None)
            with self.assertRaises(ValueError):
                load_api_embeddings()
        finally:
            _restore_env(saved)


class _Message:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str | None) -> None:
        self.message = _Message(content)


class _ChatResponse:
    def __init__(self, content: str | None) -> None:
        self.choices = [] if content is None else [_Choice(content)]


class _Completions:
    def __init__(self, content: str | None) -> None:
        self.content = content
        self.messages: list[dict[str, str]] = []

    def create(
        self,
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        messages: list[dict[str, str]],
    ) -> _ChatResponse:
        self.messages.extend(messages)
        return _ChatResponse(self.content)


class _Chat:
    def __init__(self, completions: _Completions) -> None:
        self.completions = completions


class _ChatClient:
    def __init__(self, content: str | None) -> None:
        self.chat = _Chat(_Completions(content))


class GenerationTests(unittest.TestCase):
    def test_no_parents_skips_the_api(self) -> None:
        client = _ChatClient("不会被用到")
        module = GenerationIntegrationModule(0.1, 32, client, "test-model")
        self.assertEqual(module.answer("射程", []), NO_EVIDENCE)
        self.assertEqual(client.chat.completions.messages, [])

    def test_prompt_keeps_braces_from_the_source(self) -> None:
        client = _ChatClient("150 尺")
        module = GenerationIntegrationModule(0.1, 32, client, "test-model")
        parent = RagPiece("{8d6}", {"location": "docs/Spells/Fireball.md#火球术"})
        self.assertEqual(module.answer("问{context}", [parent]), "150 尺")
        messages = client.chat.completions.messages
        self.assertEqual([item["role"] for item in messages], ["system", "user"])
        self.assertEqual(messages[1]["content"], "问{context}")
        system_text = messages[0]["content"]
        self.assertNotIn("问{context}", system_text)
        self.assertIn("{8d6}", system_text)
        self.assertNotIn(VERSION_SENTENCE, system_text)


class IndexTests(unittest.TestCase):
    def test_injected_embeddings_round_trip_without_api(self) -> None:
        piece = RagPiece("火球术射程", {"section_id": "srd51:spell:fireball"})
        with tempfile.TemporaryDirectory() as folder:
            index_dir = Path(folder) / "index"
            built = IndexConstructionModule(index_dir, _FixedEmbeddings())
            built.build([piece])
            built.save(CHUNK_SHA256)
            loaded = IndexConstructionModule(index_dir, _FixedEmbeddings())
            store = loaded.load(CHUNK_SHA256, FAKE_EMBEDDING_MODEL)
            self.assertIsNotNone(store)
            assert store is not None
            found = store.similarity_search("射程", k=1)
            self.assertEqual(found[0].page_content, "火球术射程")

    def test_changed_chunk_hash_skips_unpickle(self) -> None:
        piece = RagPiece("火球术射程", {"section_id": "srd51:spell:fireball"})
        with tempfile.TemporaryDirectory() as folder:
            index_dir = Path(folder) / "index"
            built = IndexConstructionModule(index_dir, _FixedEmbeddings())
            built.build([piece])
            built.save(CHUNK_SHA256)
            _replace_chunk_hash(index_dir)
            loaded = IndexConstructionModule(index_dir, _FixedEmbeddings())
            store = loaded.load(CHUNK_SHA256, FAKE_EMBEDDING_MODEL)
            self.assertIsNone(store)


def _replace_chunk_hash(index_dir: Path) -> None:
    path = index_dir / MANIFEST_NAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[MANIFEST_CHUNK_SHA256] = "b" * SHA256_HEX_CHARS
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _set_compare_env(model_name: str, base_url: str) -> dict[str, str | None]:
    names = (
        COMPARE_EMBEDDING_API_KEY,
        COMPARE_EMBEDDING_BASE_URL,
        COMPARE_EMBEDDING_MODEL,
    )
    saved = {name: os.environ.get(name) for name in names}
    os.environ[COMPARE_EMBEDDING_API_KEY] = "test-key"
    os.environ[COMPARE_EMBEDDING_BASE_URL] = base_url
    os.environ[COMPARE_EMBEDDING_MODEL] = model_name
    return saved


def _restore_env(saved: dict[str, str | None]) -> None:
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
