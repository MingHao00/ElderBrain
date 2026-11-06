"""从已经切好的块清单装入子块和父节。

父节按 section_id 拼回，对应 All-in-RAG 第 8 章的父子文档。
不再按菜谱标题重切。
"""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RagPiece:
    """一块检索文本，或一节交给模型的父节。"""

    text: str
    metadata: dict[str, str]


class DataPreparationModule:
    """读取中文 5.1 块清单。"""

    def __init__(self, chunk_file: Path) -> None:
        self.chunk_file = chunk_file
        self.children: list[RagPiece] = []
        self.parents: dict[str, RagPiece] = {}

    def load(self) -> list[RagPiece]:
        """装入子块，并按节拼出父节。文件不存在时抛出 FileNotFoundError。"""
        rows = _read_rows(self.chunk_file)
        grouped: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            grouped.setdefault(row["section_id"], []).append(row)
        children: list[RagPiece] = []
        parents: dict[str, RagPiece] = {}
        for section_id, group in grouped.items():
            ordered = sorted(group, key=_chunk_index)
            parents[section_id] = _parent(section_id, ordered)
            children.extend(_child(row) for row in ordered)
        self.children = children
        self.parents = parents
        return children

    def parent_sections(self, children: list[RagPiece]) -> list[RagPiece]:
        """命中的子块按节去重。先命中的节排在前面。"""
        ordered: list[RagPiece] = []
        seen: set[str] = set()
        for child in children:
            section_id = child.metadata["section_id"]
            if section_id in seen:
                continue
            seen.add(section_id)
            parent = self.parents.get(section_id)
            if parent is not None:
                ordered.append(parent)
        return ordered


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"块清单不存在: {path}")
    rows: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _chunk_index(row: dict[str, str]) -> int:
    return int(row["chunk_id"].rsplit("#", 1)[-1])


def _child(row: dict[str, str]) -> RagPiece:
    return RagPiece(text=row["embed_text"], metadata=_meta(row, "child"))


def _parent(section_id: str, rows: list[dict[str, str]]) -> RagPiece:
    body = "\n\n".join(row["text"] for row in rows)
    meta = _meta(rows[0], "parent")
    meta["section_id"] = section_id
    return RagPiece(text=body, metadata=meta)


def _meta(row: dict[str, str], doc_type: str) -> dict[str, str]:
    return {
        "chunk_id": row["chunk_id"],
        "section_id": row["section_id"],
        "entry_id": row["entry_id"],
        "entry_path": row["entry_path"],
        "location": row["location"],
        "rule_version": row["rule_version"],
        "source_id": row["source_id"],
        "doc_type": doc_type,
    }
