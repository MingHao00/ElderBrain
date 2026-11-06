"""RAG 主体的路径和调用参数。密钥只从环境变量读取。"""

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHUNK_FILE = REPO_ROOT / "parse" / "catalog" / "src-srd51-zh.chunks-1024.jsonl"
DEFAULT_INDEX_DIR = REPO_ROOT / "rag" / "vector_index"

TOP_K = 5
TEMPERATURE = 0.1
MAX_TOKENS = 2048
RULE_VERSION = "5.1"
SOURCE_ID = "src-srd51-zh"


def require_env(name: str) -> str:
    """读取必填环境变量。空白视为未设置。"""
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"请设置 {name} 环境变量")
    return value


@dataclass(frozen=True)
class RagConfig:
    """一份可运行的 RAG 配置。"""

    chunk_file: Path = DEFAULT_CHUNK_FILE
    index_dir: Path = DEFAULT_INDEX_DIR
    top_k: int = TOP_K
    temperature: float = TEMPERATURE
    max_tokens: int = MAX_TOKENS
