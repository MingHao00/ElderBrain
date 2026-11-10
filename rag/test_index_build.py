"""重建索引写入新版本。只用假嵌入和假存储，不连库，不访问嵌入接口。"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from admin_store import (
    INDEX_VERSION_MAX_CHARS,
    INDEX_VERSION_MIN_CHARS,
    publish_index,
    published_index_version,
    rebuild_index,
)
from auth_db import AuthDb
from config import RULE_VERSION, SOURCE_ID
from index_build import build_srd_index, rebuild_registered, taken_index_versions
from modules.api_embeddings import EMBEDDING3_DIMENSIONS
from modules.vector_rows import ChunkVector

ACTOR = "u_indexbuilder"
WHEN = datetime(2026, 11, 10, 2, 47, tzinfo=timezone.utc)
KEPT = "idx-kept-pointer"
SUFFIX_A = "aaaa1111"
SUFFIX_B = "bbbb2222"
WRONG_DIMENSION = 2
FOREIGN_SOURCE = "src-other"
FOREIGN_RULE = "5.2"
_MARKS = frozenset("._-")


class FakeEmbed:
    """记录正文。可以返回指定向量，或在调用时失败。"""

    def __init__(self) -> None:
        self.seen: list[list[str]] = []
        self.vectors: list[list[float]] | None = None
        self.error: BaseException | None = None

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.error is not None:
            raise self.error
        self.seen.append(list(texts))
        if self.vectors is not None:
            return [list(vector) for vector in self.vectors]
        return [_unit_vector() for _text in texts]


class FakeStore:
    """记下替换和删除。不连接数据库。"""

    def __init__(self) -> None:
        self.replaced: list[tuple[str, tuple[ChunkVector, ...]]] = []
        self.deleted: list[str] = []
        self.error: BaseException | None = None

    def replace_version(
        self,
        index_version: str,
        chunks: Sequence[ChunkVector],
    ) -> None:
        if self.error is not None:
            raise self.error
        self.replaced.append((index_version, tuple(chunks)))

    def delete_version(self, index_version: str) -> None:
        self.deleted.append(index_version)


class IndexBuildTests(unittest.TestCase):
    def test_rebuild_registers_version_and_keeps_pointer(self) -> None:
        path = _catalog(self, [_row(0, "火球术射程"), _row(1, "火球术伤害")])
        db = _db(self)
        _publish_kept(db)
        embed = FakeEmbed()
        store = FakeStore()

        def build() -> str:
            return build_srd_index(
                path,
                embed.embed_documents,
                store.replace_version,
                taken_index_versions(db),
                suffix=lambda: SUFFIX_A,
                clock=lambda: WHEN,
            )

        version = rebuild_index(db, ACTOR, build, WHEN)
        self.assertEqual(embed.seen, [["火球术射程", "火球术伤害"]])
        self.assertEqual(len(store.replaced), 1)
        self.assertEqual(store.replaced[0][0], version)
        self.assertTrue(_version_ok(version))
        chunks = store.replaced[0][1]
        self.assertEqual(chunks[0].piece.text, "火球术射程")
        self.assertEqual(len(chunks[0].embedding), EMBEDDING3_DIMENSIONS)
        self.assertIn(version, taken_index_versions(db))
        self.assertEqual(published_index_version(db), KEPT)
        self.assertEqual(store.deleted, [])

    def test_two_rebuilds_register_distinct_versions(self) -> None:
        path = _catalog(self, [_row(0, "火球术射程")])
        db = _db(self)
        _publish_kept(db)
        embed = FakeEmbed()
        store = FakeStore()
        tokens = iter((SUFFIX_A, SUFFIX_B))

        def build() -> str:
            return build_srd_index(
                path,
                embed.embed_documents,
                store.replace_version,
                taken_index_versions(db),
                suffix=lambda: next(tokens),
                clock=lambda: WHEN,
            )

        first = rebuild_index(db, ACTOR, build, WHEN)
        second = rebuild_index(db, ACTOR, build, WHEN)
        self.assertNotEqual(first, second)
        self.assertTrue(_version_ok(first))
        self.assertTrue(_version_ok(second))
        self.assertEqual([item[0] for item in store.replaced], [first, second])
        self.assertEqual(taken_index_versions(db), {KEPT, first, second})
        self.assertEqual(published_index_version(db), KEPT)

    def test_occupied_version_is_not_written_again(self) -> None:
        path = _catalog(self, [_row(0, "火球术射程")])
        embed = FakeEmbed()
        store = FakeStore()
        first = build_srd_index(
            path,
            embed.embed_documents,
            store.replace_version,
            set(),
            suffix=lambda: SUFFIX_A,
            clock=lambda: WHEN,
        )
        blocked = FakeStore()
        with self.assertRaises(ValueError):
            build_srd_index(
                path,
                embed.embed_documents,
                blocked.replace_version,
                {first},
                suffix=lambda: SUFFIX_A,
                clock=lambda: WHEN,
            )
        self.assertEqual(blocked.replaced, [])
        self.assertEqual([item[0] for item in store.replaced], [first])

    def test_embed_failure_skips_store_and_sqlite(self) -> None:
        path = _catalog(self, [_row(0, "火球术射程")])
        db = _db(self)
        _publish_kept(db)
        embed = FakeEmbed()
        embed.error = RuntimeError("embed down")
        store = FakeStore()

        def build() -> str:
            return build_srd_index(
                path,
                embed.embed_documents,
                store.replace_version,
                taken_index_versions(db),
            )

        with self.assertRaises(RuntimeError):
            rebuild_registered(db, ACTOR, build, store.delete_version, WHEN)
        self.assertEqual(store.replaced, [])
        self.assertEqual(store.deleted, [])
        self.assertEqual(taken_index_versions(db), {KEPT})
        self.assertEqual(published_index_version(db), KEPT)

    def test_replace_failure_leaves_sqlite_unchanged(self) -> None:
        path = _catalog(self, [_row(0, "火球术射程")])
        db = _db(self)
        _publish_kept(db)
        embed = FakeEmbed()
        store = FakeStore()
        store.error = RuntimeError("pg down")

        def build() -> str:
            return build_srd_index(
                path,
                embed.embed_documents,
                store.replace_version,
                taken_index_versions(db),
                suffix=lambda: SUFFIX_A,
                clock=lambda: WHEN,
            )

        with self.assertRaises(RuntimeError):
            rebuild_registered(db, ACTOR, build, store.delete_version, WHEN)
        self.assertEqual(store.replaced, [])
        self.assertEqual(store.deleted, [])
        self.assertEqual(taken_index_versions(db), {KEPT})
        self.assertEqual(published_index_version(db), KEPT)

    def test_empty_catalog_does_not_embed(self) -> None:
        path = _catalog(self, [])
        embed = FakeEmbed()
        store = FakeStore()
        with self.assertRaises(ValueError):
            build_srd_index(path, embed.embed_documents, store.replace_version, set())
        self.assertEqual(embed.seen, [])
        self.assertEqual(store.replaced, [])

    def test_missing_catalog_does_not_embed(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "missing.jsonl"
        embed = FakeEmbed()
        store = FakeStore()
        with self.assertRaises(FileNotFoundError):
            build_srd_index(path, embed.embed_documents, store.replace_version, set())
        self.assertEqual(embed.seen, [])
        self.assertEqual(store.replaced, [])

    def test_wrong_dimension_does_not_write(self) -> None:
        path = _catalog(self, [_row(0, "火球术射程")])
        embed = FakeEmbed()
        embed.vectors = [[0.0] * WRONG_DIMENSION]
        store = FakeStore()
        with self.assertRaises(ValueError):
            build_srd_index(
                path,
                embed.embed_documents,
                store.replace_version,
                set(),
                suffix=lambda: SUFFIX_A,
                clock=lambda: WHEN,
            )
        self.assertEqual(embed.seen, [["火球术射程"]])
        self.assertEqual(store.replaced, [])

    def test_wrong_count_does_not_write(self) -> None:
        path = _catalog(self, [_row(0, "火球术射程"), _row(1, "火球术伤害")])
        embed = FakeEmbed()
        embed.vectors = [_unit_vector()]
        store = FakeStore()
        with self.assertRaises(ValueError):
            build_srd_index(path, embed.embed_documents, store.replace_version, set())
        self.assertEqual(len(embed.seen), 1)
        self.assertEqual(store.replaced, [])

    def test_foreign_source_does_not_embed(self) -> None:
        path = _catalog(self, [_row(0, "别的书", source=FOREIGN_SOURCE)])
        self._reject_before_embed(path)

    def test_foreign_rule_does_not_embed(self) -> None:
        path = _catalog(self, [_row(0, "别的版", rule=FOREIGN_RULE)])
        self._reject_before_embed(path)

    def _reject_before_embed(self, path: Path) -> None:
        embed = FakeEmbed()
        store = FakeStore()
        with self.assertRaises(ValueError):
            build_srd_index(path, embed.embed_documents, store.replace_version, set())
        self.assertEqual(embed.seen, [])
        self.assertEqual(store.replaced, [])


def _catalog(case: unittest.TestCase, rows: list[dict[str, str]]) -> Path:
    folder = tempfile.TemporaryDirectory()
    case.addCleanup(folder.cleanup)
    path = Path(folder.name) / "chunks.jsonl"
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path


def _row(
    index: int,
    embed_text: str,
    source: str = SOURCE_ID,
    rule: str = RULE_VERSION,
) -> dict[str, str]:
    return {
        "chunk_id": f"srd51:spell:fireball#zh#{index}",
        "section_id": "srd51:spell:fireball",
        "entry_id": "srd51:spell:fireball",
        "entry_path": "Spells > Fireball",
        "location": "docs/Spells/Fireball.md",
        "rule_version": rule,
        "source_id": source,
        "text": f"正文{index}",
        "embed_text": embed_text,
    }


def _db(case: unittest.TestCase) -> AuthDb:
    folder = tempfile.TemporaryDirectory()
    case.addCleanup(folder.cleanup)
    return AuthDb(Path(folder.name) / "auth.sqlite")


def _publish_kept(db: AuthDb) -> None:
    rebuild_index(db, ACTOR, lambda: KEPT, WHEN)
    publish_index(db, ACTOR, KEPT, WHEN)


def _unit_vector() -> list[float]:
    values = [0.0] * EMBEDDING3_DIMENSIONS
    values[0] = 1.0
    return values


def _version_ok(value: str) -> bool:
    length = len(value)
    if length < INDEX_VERSION_MIN_CHARS or length > INDEX_VERSION_MAX_CHARS:
        return False
    if not value[0].isascii() or not value[0].isalpha():
        return False
    return all(ch.isascii() and (ch.isalnum() or ch in _MARKS) for ch in value)


if __name__ == "__main__":
    unittest.main()
