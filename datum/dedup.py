"""Exact duplicate detection indexes."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any, Protocol

from .config import DedupOptions
from .errors import BuildError
from .util import canonical_json


def draft_digest(data: Any) -> bytes:
    try:
        canonical = canonical_json(data)
    except (TypeError, ValueError) as error:
        raise ValueError(f"record cannot be canonicalized as JSON: {error}") from error
    return hashlib.sha256(canonical.encode("utf-8")).digest()


class DedupIndex(Protocol):
    def add(self, digest: bytes) -> bool:
        ...

    def close(self) -> None:
        ...


class MemoryDedupIndex:
    def __init__(self) -> None:
        self._seen: set[bytes] = set()

    def add(self, digest: bytes) -> bool:
        if digest in self._seen:
            return False
        self._seen.add(digest)
        return True

    def close(self) -> None:
        self._seen.clear()


class SqliteDedupIndex:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.connection = sqlite3.connect(path)
            self.connection.execute("PRAGMA journal_mode=OFF")
            self.connection.execute("PRAGMA synchronous=OFF")
            self.connection.execute("PRAGMA temp_store=FILE")
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS seen (digest BLOB PRIMARY KEY) WITHOUT ROWID"
            )
            self.connection.commit()
        except sqlite3.Error as error:
            raise BuildError(f"cannot initialise sqlite deduplication index: {error}") from error
        self._pending = 0
        self._closed = False

    def add(self, digest: bytes) -> bool:
        try:
            cursor = self.connection.execute("INSERT OR IGNORE INTO seen(digest) VALUES (?)", (digest,))
            inserted = cursor.rowcount != 0
            self._pending += 1
            if self._pending >= 50000:
                self.connection.commit()
                self._pending = 0
            return inserted
        except sqlite3.Error as error:
            raise BuildError(f"sqlite deduplication failed: {error}") from error

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.connection.commit()
            self.connection.close()
        except sqlite3.Error as error:
            raise BuildError(f"cannot close sqlite deduplication index: {error}") from error
        finally:
            self._closed = True


def make_dedup_index(options: DedupOptions, work_directory: Path) -> DedupIndex:
    if options.index == "sqlite":
        return SqliteDedupIndex(work_directory / "dedup.sqlite3")
    return MemoryDedupIndex()
