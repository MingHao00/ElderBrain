"""用已建好的智谱 embedding-3 索引跑召回。不重建索引，不调用生成模型。"""

import os

from config import REPO_ROOT, RagConfig
from modules.api_embeddings import COMPARE_EMBEDDING_API_KEY, load_embedding3
from run_recall import CHUNK_FILE, execute
from system import ElderBrainRag

RUN_ID = "2026-11-05-02"
INDEX_DIR = REPO_ROOT / "rag" / "vector_index_embedding3"
ERROR_TEXT_LIMIT = 500


def main() -> None:
    try:
        _recall()
    except Exception as exc:
        print(_public_error(exc))
        raise SystemExit(1) from None


def _recall() -> None:
    embedder = load_embedding3()
    rag = ElderBrainRag(RagConfig(chunk_file=CHUNK_FILE, index_dir=INDEX_DIR))
    rag.index.embeddings = embedder
    execute(RUN_ID, rag, embedder.model_name)


def _public_error(exc: Exception) -> str:
    secret = os.getenv(COMPARE_EMBEDDING_API_KEY, "").strip()
    text = f"{type(exc).__name__}: {exc}"
    if secret:
        text = text.replace(secret, "[密钥]")
    return text[:ERROR_TEXT_LIMIT]


if __name__ == "__main__":
    main()
