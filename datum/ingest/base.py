"""Contracts shared by ingestion adapters."""

from __future__ import annotations

import hashlib
import io
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterator, Mapping, Protocol, TextIO

from ..config import Config
from ..errors import ExtractionError, InputError


@dataclass(frozen=True)
class ExtractedRecord:
    ordinal: int
    kind: str
    locator: str
    text: str | None = None
    data: Any = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecordIssue:
    code: str
    message: str
    line: int | None = None
    ordinal: int | None = None


@dataclass(frozen=True)
class ExtractionSummary:
    file_format: str
    adapter: str
    adapter_version: str
    file_sha256: str
    size_bytes: int
    encoding: str | None
    records_extracted: int
    malformed_records: int = 0
    encoding_replacements: int = 0
    warnings: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


RecordCallback = Callable[[ExtractedRecord], None]
IssueCallback = Callable[[RecordIssue], None]


class Adapter(Protocol):
    name: str
    version: str

    def extract(
        self,
        path: Path,
        detection_format: str,
        config: Config,
        on_record: RecordCallback,
        on_issue: IssueCallback,
    ) -> ExtractionSummary:
        ...


def resolve_encoding(requested: str, head: bytes) -> str:
    if requested not in ("auto", ""):
        return requested
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if head.startswith(b"\xff\xfe\x00\x00") or head.startswith(b"\x00\x00\xfe\xff"):
        return "utf-32"
    if head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff"):
        return "utf-16"
    return "utf-8"


def _replacement_count(text: str) -> int:
    return text.count("\ufffd")


def decode_bytes(data: bytes, encoding: str, errors: str) -> tuple[str, int]:
    try:
        text = data.decode(encoding, errors=errors)
    except UnicodeError as error:
        raise ExtractionError(f"cannot decode input as {encoding}: {error}") from error
    replacements = _replacement_count(text) if errors == "replace" else 0
    return text, replacements


@contextmanager
def hashing_text_reader(
    path: Path,
    encoding: str,
    errors: str,
) -> Iterator[tuple[TextIO, "HashingReader"]]:
    from ..util import HashingReader

    raw = path.open("rb")
    hashing = HashingReader(raw)
    buffered = io.BufferedReader(hashing, buffer_size=128 * 1024)
    text = io.TextIOWrapper(buffered, encoding=encoding, errors=errors, newline="")
    try:
        yield text, hashing
    finally:
        try:
            text.read()
        except (OSError, UnicodeError, ValueError):
            pass
        try:
            text.close()
        except (OSError, UnicodeError, ValueError):
            pass
        try:
            raw.close()
        except OSError:
            pass


def read_all_with_digest(path: Path, max_bytes: int) -> tuple[bytes, str, int]:
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    total = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ExtractionError(f"file exceeds limits.max_file_bytes ({max_bytes} bytes)")
            digest.update(chunk)
            chunks.append(chunk)
    return b"".join(chunks), digest.hexdigest(), total


def head_bytes(path: Path, size: int = 4) -> bytes:
    try:
        with path.open("rb") as handle:
            return handle.read(size)
    except OSError:
        return b""


def check_file_size(path: Path, max_bytes: int) -> int:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise InputError(f"cannot stat file: {error}") from error
    if size > max_bytes:
        raise ExtractionError(f"file size {size} exceeds limits.max_file_bytes ({max_bytes})")
    return size


def count_replacements(text: str) -> int:
    return _replacement_count(text)


def detect_encoding_for_path(path: Path, config: Config) -> str:
    return resolve_encoding(config.source.encoding, head_bytes(path))
