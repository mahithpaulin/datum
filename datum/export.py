"""Deterministic JSONL dataset and sidecar writers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config
from .errors import BuildError
from .util import JSON_SEPARATORS


SPLIT_ORDER = ("train", "validation", "test")
SPLIT_FILENAMES = {
    "dataset": "dataset.jsonl",
    "train": "train.jsonl",
    "validation": "validation.jsonl",
    "test": "test.jsonl",
}
SIDECAR_FILENAME = "provenance.jsonl"
REJECTED_FILENAME = "rejected.jsonl"


def split_for(digest: bytes, config: Config) -> str:
    if not config.split.enabled:
        return "dataset"
    seed = str(config.split.seed).encode("utf-8")
    bucket = int.from_bytes(hashlib.sha256(seed + b":" + digest).digest()[:8], "big") / 2**64
    if bucket < config.split.train:
        return "train"
    if bucket < config.split.train + config.split.validation:
        return "validation"
    return "test"


def serialize_json_line(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=JSON_SEPARATORS,
            allow_nan=False,
        ) + "\n"
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise BuildError(f"cannot serialize output record as JSON: {error}") from error


class JsonlWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self.handle = path.open("w", encoding="utf-8", newline="\n", buffering=1024 * 1024)
        except OSError as error:
            raise BuildError(f"cannot create output file {path}: {error}") from error
        self.lines = 0

    def write(self, value: Any) -> int:
        self.handle.write(serialize_json_line(value))
        self.lines += 1
        return self.lines

    def close(self) -> None:
        try:
            self.handle.flush()
            self.handle.close()
        except OSError as error:
            raise BuildError(f"cannot write output file {self.path}: {error}") from error

    @property
    def bytes_written(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0


@dataclass
class ExportStats:
    counts: dict[str, int] = field(default_factory=dict)
    bytes_by_file: dict[str, int] = field(default_factory=dict)
    output_bytes: int = 0
    sidecar_lines: int = 0
    sidecar_bytes: int = 0
    rejected_lines: int = 0
    rejected_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "split_counts": dict(sorted(self.counts.items())),
            "split_bytes": dict(sorted(self.bytes_by_file.items())),
            "dataset_bytes": self.output_bytes,
            "sidecar_lines": self.sidecar_lines,
            "sidecar_bytes": self.sidecar_bytes,
            "rejected_lines": self.rejected_lines,
            "rejected_bytes": self.rejected_bytes,
        }


class DatasetWriters:
    def __init__(self, directory: Path, config: Config) -> None:
        self.directory = directory
        self.config = config
        self.dataset_writers: dict[str, JsonlWriter] = {}
        self.counts: dict[str, int] = {}
        split_names = SPLIT_ORDER if config.split.enabled else ("dataset",)
        for split in split_names:
            self.dataset_writers[split] = JsonlWriter(directory / SPLIT_FILENAMES[split])
            self.counts[split] = 0
        self.sidecar = (
            JsonlWriter(directory / SIDECAR_FILENAME)
            if config.provenance.mode in ("sidecar", "both")
            else None
        )
        self.rejected = JsonlWriter(directory / REJECTED_FILENAME)
        self.closed = False

    def write_dataset(self, split: str, value: Any) -> tuple[str, int]:
        writer = self.dataset_writers.get(split)
        if writer is None:
            raise BuildError(f"internal error: unknown split '{split}'")
        line = writer.write(value)
        self.counts[split] += 1
        return SPLIT_FILENAMES[split], line

    def write_sidecar(self, value: Any) -> None:
        if self.sidecar is not None:
            self.sidecar.write(value)

    def write_rejected(self, value: Any) -> None:
        self.rejected.write(value)

    def finish(self) -> ExportStats:
        if self.closed:
            raise BuildError("internal error: dataset writers already closed")
        stats = ExportStats(counts=dict(self.counts))
        for split, writer in self.dataset_writers.items():
            writer.close()
            stats.bytes_by_file[SPLIT_FILENAMES[split]] = writer.bytes_written
        stats.output_bytes = sum(stats.bytes_by_file.values())
        if self.sidecar is not None:
            self.sidecar.close()
            stats.sidecar_lines = self.sidecar.lines
            stats.sidecar_bytes = self.sidecar.bytes_written
        self.rejected.close()
        stats.rejected_lines = self.rejected.lines
        stats.rejected_bytes = self.rejected.bytes_written
        self.closed = True
        return stats

    def abort(self) -> None:
        for writer in self.dataset_writers.values():
            try:
                writer.handle.close()
            except Exception:
                pass
        if self.sidecar is not None:
            try:
                self.sidecar.handle.close()
            except Exception:
                pass
        try:
            self.rejected.handle.close()
        except Exception:
            pass
        self.closed = True
