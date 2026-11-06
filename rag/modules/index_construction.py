"""把外部嵌入接口返回的向量写入本地 FAISS。"""

import hashlib
import json
import pickle
from pathlib import Path
from typing import Any

import faiss
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from config import require_env
from modules.api_embeddings import EMBEDDING_MODEL, load_api_embeddings
from modules.data_preparation import RagPiece

MANIFEST_NAME = "manifest.json"
INDEX_NAME = "index"
INDEX_PKL_NAME = "index.pkl"
INDEX_FAISS_NAME = "index.faiss"
MANIFEST_CHUNK_SHA256 = "chunk_sha256"
MANIFEST_EMBEDDING_MODEL = "embedding_model"
MANIFEST_VECTOR_DIM = "vector_dim"
MANIFEST_INDEX_SHA256 = "index_pkl_sha256"
# 分块读文件，避免把整份索引一次读进内存。
HASH_READ_BYTES = 65536
MIN_VECTOR_DIM = 1


class IndexConstructionModule:
    """构建或加载本地向量索引。嵌入客户端推迟到真正建库或加载时。"""

    def __init__(self, index_dir: Path, embeddings: Embeddings | None = None) -> None:
        self.index_dir = index_dir
        self.embeddings = embeddings
        self.vectorstore: FAISS | None = None

    def embedding_model_name(self) -> str:
        """当前嵌入模型名。未注入客户端时只读环境变量，不发起请求。"""
        if self.embeddings is None:
            return require_env(EMBEDDING_MODEL)
        return _model_name(self.embeddings)

    def build(self, children: list[RagPiece]) -> FAISS:
        """用子块的 embed 文本建索引。空列表直接报错。"""
        if not children:
            raise ValueError("没有可索引的块")
        self.vectorstore = FAISS.from_documents(_as_documents(children), self._client())
        return self.vectorstore

    def save(self, chunk_sha256: str) -> None:
        """写出索引，并在同目录记下块清单、模型和向量文件的摘要。"""
        if self.vectorstore is None:
            raise ValueError("请先构建向量索引")
        if not chunk_sha256.strip():
            raise ValueError("块清单摘要不能为空")
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.vectorstore.save_local(str(self.index_dir), INDEX_NAME)
        _write_manifest(
            self.index_dir,
            chunk_sha256,
            self.embedding_model_name(),
            int(self.vectorstore.index.d),
        )

    def load(self, chunk_sha256: str, embedding_model: str) -> FAISS | None:
        """摘要不一致或文件损坏时返回 None，由调用方重建。"""
        if not _manifest_matches(self.index_dir, chunk_sha256, embedding_model):
            return None
        return self._load_checked()

    def _client(self) -> Embeddings:
        if self.embeddings is None:
            self.embeddings = load_api_embeddings()
        return self.embeddings

    def _load_checked(self) -> FAISS | None:
        # 块哈希、模型、维度和 index.pkl 哈希都已经对上，才反序列化。
        embeddings = self._client()
        try:
            self.vectorstore = FAISS.load_local(
                str(self.index_dir),
                embeddings,
                index_name=INDEX_NAME,
                allow_dangerous_deserialization=True,
            )
        except (OSError, ValueError, RuntimeError, pickle.UnpicklingError) as exc:
            print(f"加载向量索引失败，将重建: {exc}")
            self.vectorstore = None
            return None
        return self.vectorstore


def file_sha256(path: Path) -> str | None:
    """文件不存在或读失败时返回 None，调用方据此拒绝加载。"""
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while True:
                block = handle.read(HASH_READ_BYTES)
                if not block:
                    break
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def _as_documents(pieces: list[RagPiece]) -> list[Document]:
    return [
        Document(page_content=piece.text, metadata=dict(piece.metadata))
        for piece in pieces
    ]


def _model_name(embeddings: Embeddings) -> str:
    name = getattr(embeddings, "model_name", "")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("嵌入模型名不能为空")
    return name.strip()


def _write_manifest(
    index_dir: Path,
    chunk_sha256: str,
    embedding_model: str,
    vector_dim: int,
) -> None:
    index_hash = file_sha256(index_dir / INDEX_PKL_NAME)
    if index_hash is None:
        raise ValueError("索引文件没有写出来")
    payload = {
        MANIFEST_CHUNK_SHA256: chunk_sha256,
        MANIFEST_EMBEDDING_MODEL: embedding_model,
        MANIFEST_VECTOR_DIM: vector_dim,
        MANIFEST_INDEX_SHA256: index_hash,
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    (index_dir / MANIFEST_NAME).write_text(text + "\n", encoding="utf-8")


def _manifest_matches(index_dir: Path, chunk_sha256: str, embedding_model: str) -> bool:
    manifest = _read_manifest(index_dir)
    if manifest is None:
        return False
    if not _identity_matches(manifest, chunk_sha256, embedding_model):
        return False
    return _files_match_manifest(index_dir, manifest)


def _read_manifest(index_dir: Path) -> dict[str, Any] | None:
    path = index_dir / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _identity_matches(
    manifest: dict[str, Any],
    chunk_sha256: str,
    embedding_model: str,
) -> bool:
    if manifest.get(MANIFEST_CHUNK_SHA256) != chunk_sha256:
        return False
    if manifest.get(MANIFEST_EMBEDDING_MODEL) != embedding_model:
        return False
    return _is_vector_dim(manifest.get(MANIFEST_VECTOR_DIM))


def _is_vector_dim(value: object) -> bool:
    # bool 是 int 的子类，不能把它当成维度。
    return isinstance(value, int) and not isinstance(value, bool) and value >= MIN_VECTOR_DIM


def _files_match_manifest(index_dir: Path, manifest: dict[str, Any]) -> bool:
    expected_hash = manifest.get(MANIFEST_INDEX_SHA256)
    if not isinstance(expected_hash, str) or not expected_hash:
        return False
    if file_sha256(index_dir / INDEX_PKL_NAME) != expected_hash:
        return False
    return _faiss_dimension(index_dir) == manifest[MANIFEST_VECTOR_DIM]


def _faiss_dimension(index_dir: Path) -> int | None:
    """只读 faiss 文件的维度，不打开 index.pkl。"""
    path = index_dir / INDEX_FAISS_NAME
    if not path.is_file():
        return None
    try:
        index = faiss.read_index(str(path))
    except (OSError, RuntimeError, ValueError):
        return None
    dimension = int(index.d)
    if dimension < MIN_VECTOR_DIM:
        return None
    return dimension
