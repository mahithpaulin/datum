"""Shared deterministic utilities."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Iterator


JSON_SEPARATORS = (",", ":")


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=JSON_SEPARATORS,
        allow_nan=False,
    )


def json_line(value: Any) -> str:
    return canonical_json(value) + "\n"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8", "surrogatepass"))


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def human_bytes(value: int | float) -> str:
    number = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(number) < 1024.0 or unit == "TiB":
            if unit == "B":
                return f"{int(number)} {unit}"
            return f"{number:.1f} {unit}"
        number /= 1024.0
    return f"{number:.1f} TiB"


def human_duration(seconds: float) -> str:
    if seconds < 1.0:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60.0:
        return f"{seconds:.2f} s"
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f"{minutes}m {remainder:.1f}s"


def safe_ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def percentile(sorted_values: list[int], fraction: float) -> int | None:
    if not sorted_values:
        return None
    if fraction <= 0:
        return sorted_values[0]
    if fraction >= 1:
        return sorted_values[-1]
    index = max(0, math.ceil(fraction * len(sorted_values)) - 1)
    return sorted_values[index]


def length_distribution(lengths: Iterable[int]) -> dict[str, Any]:
    values = sorted(lengths)
    total = sum(values)
    return {
        "count": len(values),
        "min": values[0] if values else None,
        "max": values[-1] if values else None,
        "mean": (total / len(values)) if values else None,
        "p50": percentile(values, 0.50),
        "p90": percentile(values, 0.90),
        "p99": percentile(values, 0.99),
        "total_chars": total,
    }


def redact_preview(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 3)] + "..."


def check_no_nan(value: Any, path: str = "$") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non-finite number at {path}")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"non-string object key at {path}")
            check_no_nan(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            check_no_nan(item, f"{path}[{index}]")


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def iter_jsonl(path: Path) -> Iterator[tuple[int, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            yield line_number, json.loads(line)


def reject_json_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def strict_json_loads(text: str) -> Any:
    return json.loads(text, parse_constant=reject_json_constant)


def parse_jsonl_text(text: str) -> Iterator[tuple[int, Any]]:
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        yield line_number, strict_json_loads(line)


class HashingReader(io.RawIOBase):
    """Binary reader that computes SHA-256 of every byte consumed."""

    def __init__(self, handle: BinaryIO) -> None:
        self._handle = handle
        self._digest = hashlib.sha256()
        self.bytes_read = 0
        self.finished = False

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray | memoryview) -> int:
        view = memoryview(buffer)
        count = self._handle.readinto(view)
        if count:
            self._digest.update(view[:count])
            self.bytes_read += count
        else:
            self.finished = True
        return count or 0

    def read(self, size: int = -1) -> bytes:
        data = self._handle.read(size)
        if data:
            self._digest.update(data)
            self.bytes_read += len(data)
        else:
            self.finished = True
        return data

    def readall(self) -> bytes:
        data = self._handle.read()
        if data:
            self._digest.update(data)
            self.bytes_read += len(data)
        self.finished = True
        return data

    def drain(self, chunk_size: int = 1024 * 1024) -> None:
        while self.read(chunk_size):
            pass

    @property
    def hexdigest(self) -> str:
        return self._digest.hexdigest()

    def close(self) -> None:
        self._handle.close()
        super().close()


def toml_scalar_repr(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    return json.dumps(value)
