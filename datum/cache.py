"""Content-addressed incremental cache for per-file drafts."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from . import PIPELINE_VERSION, __version__
from .config import Config
from .errors import BuildError
from .schema import SchemaSpec
from .util import canonical_json, read_json, sha256_file

CACHE_SCHEMA_VERSION = 1


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    invalid: int = 0
    writes: int = 0
    write_errors: int = 0
    hash_seconds: float = 0.0
    read_seconds: float = 0.0
    write_seconds: float = 0.0
    entry_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("hash_seconds", "read_seconds", "write_seconds"):
            data[key] = round(float(data[key]), 6)
        return data


@dataclass(frozen=True)
class CacheHit:
    path: Path
    payload: Mapping[str, Any]
    meta: Mapping[str, Any]


class FileCache:
    def __init__(
        self,
        config: Config,
        schema: SchemaSpec,
    ) -> None:
        self.config = config
        self.schema = schema
        self.root = config.cache_directory()
        self.enabled = config.cache.enabled
        self.verify_integrity = config.cache.verify_integrity
        self.stats = CacheStats()
        if self.enabled:
            try:
                (self.root / "objects").mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise BuildError(f"cannot create cache directory {self.root}: {error}") from error

    def key(self, file_sha256: str, adapter: str, adapter_version: str, file_format: str) -> str:
        return hashlib.sha256(
            canonical_json(
                {
                    "cache_schema_version": CACHE_SCHEMA_VERSION,
                    "datum_version": __version__,
                    "pipeline_version": PIPELINE_VERSION,
                    "pipeline_fingerprint": self.config.pipeline_fingerprint(),
                    "schema_fingerprint": self.schema.fingerprint(),
                    "adapter": adapter,
                    "adapter_version": adapter_version,
                    "file_format": file_format,
                    "file_sha256": file_sha256,
                }
            ).encode("utf-8")
        ).hexdigest()

    def entry_path(self, key: str) -> Path:
        return self.root / "objects" / key[:2] / key

    def get(
        self,
        key: str,
        *,
        file_sha256: str,
        adapter: str,
        adapter_version: str,
        file_format: str,
    ) -> CacheHit | None:
        if not self.enabled:
            return None
        entry = self.entry_path(key)
        meta_path = entry / "meta.json"
        records_path = entry / "drafts.jsonl"
        if not meta_path.exists() or not records_path.exists():
            self.stats.misses += 1
            return None
        try:
            meta = read_json(meta_path)
            if meta.get("cache_schema_version") != CACHE_SCHEMA_VERSION:
                raise ValueError("cache schema version changed")
            if meta.get("key") != key:
                raise ValueError("cache key mismatch")
            if meta.get("file_sha256") != file_sha256:
                raise ValueError("cached file hash mismatch")
            if meta.get("adapter") != adapter or meta.get("adapter_version") != adapter_version:
                raise ValueError("cached adapter mismatch")
            if meta.get("file_format") != file_format:
                raise ValueError("cached format mismatch")
            if meta.get("pipeline_fingerprint") != self.config.pipeline_fingerprint():
                raise ValueError("cached pipeline fingerprint mismatch")
            if meta.get("schema_fingerprint") != self.schema.fingerprint():
                raise ValueError("cached schema fingerprint mismatch")
            if int(meta.get("spool_bytes", -1)) != records_path.stat().st_size:
                raise ValueError("cached draft file size mismatch")
            if self.verify_integrity:
                digest, _size = sha256_file(records_path)
                if digest != meta.get("spool_sha256"):
                    raise ValueError("cached draft checksum mismatch")
        except (OSError, ValueError, TypeError, KeyError):
            self.stats.invalid += 1
            return None
        self.stats.hits += 1
        return CacheHit(path=records_path, payload=meta.get("payload") or {}, meta=meta)

    def put(
        self,
        key: str,
        spool_path: Path,
        payload: Mapping[str, Any],
    ) -> bool:
        if not self.enabled:
            return False
        entry = self.entry_path(key)
        entry.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".tmp-cache-", dir=entry.parent))
        try:
            records_path = temporary / "drafts.jsonl"
            shutil.copyfile(spool_path, records_path)
            digest, size = sha256_file(records_path)
            meta = {
                "cache_schema_version": CACHE_SCHEMA_VERSION,
                "key": key,
                "datum_version": __version__,
                "pipeline_version": PIPELINE_VERSION,
                "pipeline_fingerprint": self.config.pipeline_fingerprint(),
                "schema_fingerprint": self.schema.fingerprint(),
                "adapter": payload.get("adapter"),
                "adapter_version": payload.get("adapter_version"),
                "file_format": payload.get("format"),
                "file_sha256": payload.get("file_sha256"),
                "file_size": payload.get("file_size"),
                "spool_sha256": digest,
                "spool_bytes": size,
                "payload": dict(payload),
            }
            (temporary / "meta.json").write_text(
                canonical_json(meta) + "\n", encoding="utf-8", newline="\n"
            )
            if entry.exists():
                shutil.rmtree(entry)
            entry.parent.mkdir(parents=True, exist_ok=True)
            temporary.rename(entry)
            self.stats.writes += 1
            self.stats.entry_bytes += size
            return True
        except OSError:
            self.stats.write_errors += 1
            shutil.rmtree(temporary, ignore_errors=True)
            return False
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "directory": str(self.root),
            **self.stats.to_dict(),
        }
