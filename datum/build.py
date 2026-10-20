"""Build orchestration: discovery, extraction, compilation, verification, publication."""

from __future__ import annotations

import os
import resource
import shutil
import tempfile
import threading
import time
import uuid
from collections import Counter, deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from .cache import FileCache
from .config import Config
from .dedup import make_dedup_index
from .discovery import DiscoveryResult, FileEntry, discover
from .errors import BuildCancelled, BuildError, ConfigError, ExtractionError
from .export import DatasetWriters, SIDECAR_FILENAME, split_for
from .filetypes import Detection, detect_file
from .ingest import get_adapter
from .manifest import build_id_for, build_manifest, environment_info, write_manifest
from .quality import (
    ErrorCollector,
    FileCounters,
    RecordCounters,
    TransformationCounters,
    build_quality_summary,
)
from .schema import LoadedSchema, load_schema, validate_record
from .transform.mapping import Draft, MappingFailure, build_drafts
from .transform.normalize import NormalizeStats
from .transform.chunking import load_tokenizer
from .util import (
    atomic_write_json,
    atomic_write_text,
    canonical_json,
    human_duration,
    redact_preview,
    sha256_file,
    sha256_text,
    strict_json_loads,
    utc_now_iso,
)
from .verify import verify_build

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None


ProgressCallback = Callable[[int, int, "FileResult"], None]


@dataclass
class FileResult:
    index: int
    entry: FileEntry
    status: str
    detection: Detection | None = None
    reason: str = ""
    digest: str = ""
    size: int = 0
    adapter: str = ""
    adapter_version: str = ""
    format: str = ""
    encoding: str | None = None
    spool_path: Path | None = None
    cache_hit: bool = False
    records_extracted: int = 0
    records_mapped: int = 0
    mapping_rejected: int = 0
    chunks_created: int = 0
    source_records_split: int = 0
    malformed_input: int = 0
    warnings: list[str] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)
    normalize_stats: dict[str, int] = field(default_factory=dict)
    encoding_replacements: int = 0
    extraction_seconds: float = 0.0
    mapping_seconds: float = 0.0
    hash_seconds: float = 0.0

    def input_entry(self, include_hash: bool) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "path": self.entry.relative_path,
            "size": self.size or self.entry.size,
            "format": self.format or "unknown",
            "status": self.status,
        }
        if self.adapter:
            entry["adapter"] = self.adapter
        if include_hash and self.digest:
            entry["sha256"] = self.digest
        if self.reason:
            entry["reason"] = self.reason
        return entry


@dataclass
class BuildResult:
    build_id: str
    status: str
    output_directory: Path
    manifest_path: Path
    quality_path: Path
    manifest: dict[str, Any]
    quality: dict[str, Any]
    files: dict[str, Any]
    records: dict[str, Any]
    splits: dict[str, int]
    cache: dict[str, Any]
    performance: dict[str, Any]
    verification_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "build_id": self.build_id,
            "status": self.status,
            "quality_status": self.quality.get("status"),
            "output_directory": str(self.output_directory),
            "manifest_path": str(self.manifest_path),
            "quality_path": str(self.quality_path),
            "files": self.files,
            "records": self.records,
            "splits": self.splits,
            "cache": self.cache,
            "performance": self.performance,
            "verification_seconds": round(self.verification_seconds, 6),
        }


@dataclass
class _WorkerContext:
    config: Config
    schema: LoadedSchema
    mapping: Mapping[str, str]
    cache: FileCache
    tokenizer: Any
    tokenizer_lock: threading.Lock
    cache_lock: threading.Lock
    spool_dir: Path
    cancel_event: threading.Event


class _BuildLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any = None

    def __enter__(self) -> "_BuildLock":
        try:
            self.handle = self.path.open("a+", encoding="utf-8")
        except OSError as error:
            raise BuildError(f"cannot open build lock {self.path}: {error}") from error
        if fcntl is not None:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                self.handle.close()
                raise BuildError(f"another DATUM build is already using {self.path.parent}") from error
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.handle is not None:
            if fcntl is not None:
                try:
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            self.handle.close()


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _validate_paths(config: Config) -> None:
    source = config.source.path.resolve()
    output = config.output.directory.resolve()
    cache = config.cache_directory().resolve()
    if _is_within(output, source) or _is_within(source, output):
        raise ConfigError("source.path and output.dir must not contain one another")
    if _is_within(cache, source) or _is_within(source, cache):
        raise ConfigError("source.path and the cache directory must not contain one another")
    if config.schema.file is not None and _is_within(config.schema.file, source):
        raise ConfigError("schema.file must not be located inside source.path")
    if config.length.oversize == "truncate" and config.schema.name != "text":
        raise ConfigError("length.oversize = 'truncate' is only supported with schema.name = 'text'")
    if config.provenance.mode in ("inline", "both"):
        loaded = load_schema(config)
        if config.provenance.inline_field in loaded.spec.fields:
            raise ConfigError(
                f"provenance.inline_field '{config.provenance.inline_field}' collides with a schema field"
            )


def _available_memory() -> int | None:
    try:
        with open("/proc/meminfo", "r", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def resolve_workers(config: Config, file_count: int, total_bytes: int = 0) -> int:
    if file_count <= 1 or config.runtime.workers == 1:
        return 1
    if config.runtime.workers == 0 and total_bytes and total_bytes < 256 * 1024:
        return 1
    if config.runtime.workers > 0:
        return min(config.runtime.workers, 64, file_count)
    cpu = os.cpu_count() or 1
    workers = min(4, cpu, file_count)
    available = _available_memory()
    if available is not None:
        workers = min(workers, max(1, int(available // (128 * 1024 * 1024))))
    return max(1, workers)


def _check_disk(config: Config) -> None:
    try:
        usage = shutil.disk_usage(config.output.directory)
    except OSError as error:
        raise BuildError(f"cannot inspect free disk space for {config.output.directory}: {error}") from error
    if usage.free < config.limits.min_free_disk_bytes:
        raise BuildError(
            f"free disk space ({usage.free} bytes) is below limits.min_free_disk_bytes "
            f"({config.limits.min_free_disk_bytes})"
        )


def _stage_path(output: Path, kind: str) -> Path:
    return output / f".datum-{kind}-{uuid.uuid4().hex[:12]}"


def _preview(value: Any, limit: int) -> str:
    pieces: list[str] = []
    remaining = limit * 2

    def walk(item: Any) -> None:
        nonlocal remaining
        if remaining <= 0:
            return
        if isinstance(item, str):
            piece = item[:remaining]
            pieces.append(piece)
            remaining -= len(piece)
        elif isinstance(item, dict):
            for child in item.values():
                walk(child)
                if remaining <= 0:
                    break
        elif isinstance(item, list):
            for child in item:
                walk(child)
                if remaining <= 0:
                    break
        elif item is not None:
            piece = str(item)[:remaining]
            pieces.append(piece)
            remaining -= len(piece)

    walk(value)
    return redact_preview(" ".join(pieces), limit)


def _record_id(path: str, digest: str, ordinal: int, chunk_index: int, suffix: str = "") -> str:
    return sha256_text(f"{path}\0{digest}\0{ordinal}\0{chunk_index}{suffix}")[:24]


def _spool_writer(path: Path) -> Any:
    try:
        return path.open("w", encoding="utf-8", newline="\n")
    except OSError as error:
        raise BuildError(f"cannot create spool file {path}: {error}") from error


def _process_file(index: int, entry: FileEntry, context: _WorkerContext) -> FileResult:
    config = context.config
    detection = detect_file(entry.path, config)
    result = FileResult(index=index, entry=entry, status="failed", detection=detection)
    if detection.error:
        result.reason = detection.error
        result.format = detection.format
        return result
    result.format = detection.format
    result.adapter = detection.adapter
    if not detection.supported:
        result.status = "skipped" if config.errors.unsupported == "skip" else "failed"
        result.reason = detection.reason
        return result
    try:
        adapter = get_adapter(detection.adapter)
    except ValueError as error:
        result.status = "failed"
        result.reason = str(error)
        return result
    result.adapter_version = adapter.version
    if entry.size > config.limits.max_file_bytes:
        result.reason = (
            f"file size {entry.size} exceeds limits.max_file_bytes "
            f"({config.limits.max_file_bytes})"
        )
        return result

    staging_spool = context.spool_dir / f"{index:07d}.jsonl"
    digest = ""
    key = ""
    if config.cache.enabled:
        started = time.perf_counter()
        try:
            digest, hashed_size = sha256_file(entry.path)
        except OSError as error:
            result.reason = f"cannot hash input file: {error}"
            return result
        result.hash_seconds = time.perf_counter() - started
        with context.cache_lock:
            context.cache.stats.hash_seconds += result.hash_seconds
        if hashed_size != entry.size:
            result.reason = f"file size changed during discovery ({entry.size} -> {hashed_size} bytes)"
            return result
        key = context.cache.key(digest, adapter.name, adapter.version, detection.format)
        read_started = time.perf_counter()
        with context.cache_lock:
            hit = context.cache.get(
                key,
                file_sha256=digest,
                adapter=adapter.name,
                adapter_version=adapter.version,
                file_format=detection.format,
            )
            context.cache.stats.read_seconds += time.perf_counter() - read_started
        if hit is not None:
            copy_started = time.perf_counter()
            try:
                shutil.copyfile(hit.path, staging_spool)
            except OSError as error:
                result.reason = f"cannot materialize cached extraction: {error}"
                return result
            payload = hit.payload
            if payload.get("file_sha256") != digest:
                result.reason = "cached extraction belongs to a different file digest"
                return result
            result.status = "processed"
            result.cache_hit = True
            result.spool_path = staging_spool
            result.digest = digest
            result.size = int(payload.get("file_size", entry.size))
            result.format = str(payload.get("format", detection.format))
            result.adapter = str(payload.get("adapter", adapter.name))
            result.adapter_version = str(payload.get("adapter_version", adapter.version))
            result.encoding = payload.get("encoding")
            result.records_extracted = int(payload.get("records_extracted", 0))
            result.records_mapped = int(payload.get("records_mapped", 0))
            result.mapping_rejected = int(payload.get("mapping_rejected", 0))
            result.chunks_created = int(payload.get("chunks_created", 0))
            result.source_records_split = int(payload.get("source_records_split", 0))
            result.malformed_input = int(payload.get("malformed_input", 0))
            result.encoding_replacements = int(payload.get("encoding_replacements", 0))
            result.warnings = list(payload.get("warnings") or [])
            result.issues = list(payload.get("issues") or [])
            result.normalize_stats = dict(payload.get("normalize_stats") or {})
            result.size = result.size or entry.size
            with context.cache_lock:
                context.cache.stats.read_seconds += time.perf_counter() - copy_started
            if config.errors.policy == "strict" and result.malformed_input:
                try:
                    staging_spool.unlink()
                except OSError:
                    pass
                result.status = "failed"
                result.spool_path = None
                result.reason = (
                    f"strict mode: {result.malformed_input} malformed input record(s)"
                )
            return result

    spool = _spool_writer(staging_spool)
    mapping_seconds = 0.0
    extraction_seconds = 0.0
    mapping_rejected = 0
    mapped = 0
    chunks = 0
    split_sources = 0
    malformed = 0
    issues: list[dict[str, Any]] = []
    file_normalize = NormalizeStats()
    started = time.perf_counter()

    def on_issue(issue: Any) -> None:
        nonlocal malformed
        malformed += 1
        item = {
            "code": issue.code,
            "message": issue.message,
            "line": issue.line,
            "ordinal": issue.ordinal,
        }
        issues.append(item)
        spool.write(
            canonical_json(
                {
                    "event": "malformed",
                    "issue": item,
                    "source": {"path": entry.relative_path, "line": issue.line, "ordinal": issue.ordinal},
                }
            )
            + "\n"
        )

    def on_record(record: Any) -> None:
        nonlocal mapping_seconds, mapping_rejected, mapped, chunks, split_sources
        mapping_started = time.perf_counter()
        try:
            if config.chunking.unit == "tokens" and context.tokenizer is not None:
                with context.tokenizer_lock:
                    drafts = build_drafts(
                        record,
                        config,
                        context.schema,
                        context.mapping,
                        file_normalize,
                        context.tokenizer,
                    )
            else:
                drafts = build_drafts(
                    record,
                    config,
                    context.schema,
                    context.mapping,
                    file_normalize,
                )
        except MappingFailure as error:
            mapping_rejected += 1
            spool.write(
                canonical_json(
                    {
                        "event": "reject",
                        "rule": "mapping_error",
                        "message": str(error),
                        "source": {
                            "path": entry.relative_path,
                            "ordinal": record.ordinal,
                            "locator": record.locator,
                            "reject_ordinal": mapping_rejected,
                        },
                    }
                )
                + "\n"
            )
            mapping_seconds += time.perf_counter() - mapping_started
            return
        mapped += 1
        chunks += len(drafts)
        if len(drafts) > 1:
            split_sources += 1
        for draft in drafts:
            source = {
                "path": entry.relative_path,
                "ordinal": record.ordinal,
                "locator": record.locator,
                "chunk_index": draft.chunk_index,
                "chunk_count": draft.chunk_count,
                "char_start": draft.char_start,
                "char_end": draft.char_end,
                "token_start": draft.token_start,
                "token_end": draft.token_end,
            }
            if record.metadata:
                source["metadata"] = dict(record.metadata)
            spool.write(
                canonical_json(
                    {
                        "event": "draft",
                        "draft": draft.to_dict(),
                        "source": source,
                    }
                )
                + "\n"
            )
        mapping_seconds += time.perf_counter() - mapping_started

    try:
        summary = adapter.extract(
            entry.path,
            detection.format,
            config,
            on_record,
            on_issue,
        )
        extraction_seconds = time.perf_counter() - started - mapping_seconds
    except (ExtractionError, OSError, UnicodeError, ValueError) as error:
        spool.close()
        result.reason = str(error)
        result.digest = digest
        result.size = entry.size
        result.spool_path = None
        try:
            staging_spool.unlink()
        except OSError:
            pass
        return result
    except MemoryError:
        spool.close()
        try:
            staging_spool.unlink()
        except OSError:
            pass
        raise
    except Exception as error:
        spool.close()
        result.reason = f"unexpected adapter failure ({type(error).__name__}): {error}"
        result.digest = digest
        result.size = entry.size
        result.spool_path = None
        try:
            staging_spool.unlink()
        except OSError:
            pass
        return result
    finally:
        try:
            spool.close()
        except Exception:
            pass

    if summary.size_bytes > config.limits.max_file_bytes:
        try:
            staging_spool.unlink()
        except OSError:
            pass
        result.reason = f"file exceeds limits.max_file_bytes ({config.limits.max_file_bytes})"
        return result
    if digest and summary.file_sha256 != digest:
        try:
            staging_spool.unlink()
        except OSError:
            pass
        result.reason = "file changed while it was being read (digest mismatch)"
        result.digest = digest
        result.size = summary.size_bytes
        return result
    result.digest = summary.file_sha256
    result.size = summary.size_bytes
    result.encoding = summary.encoding
    result.records_extracted = summary.records_extracted
    result.records_mapped = mapped
    result.mapping_rejected = mapping_rejected
    result.chunks_created = chunks
    result.source_records_split = split_sources
    result.malformed_input = summary.malformed_records
    result.encoding_replacements = summary.encoding_replacements
    result.warnings = list(summary.warnings)
    result.issues = issues
    result.normalize_stats = file_normalize.to_dict()
    result.extraction_seconds = max(0.0, extraction_seconds)
    result.mapping_seconds = mapping_seconds
    if mapped + mapping_rejected != summary.records_extracted:
        try:
            staging_spool.unlink()
        except OSError:
            pass
        result.status = "failed"
        result.reason = "internal record accounting mismatch"
        return result
    if summary.malformed_records != malformed:
        try:
            staging_spool.unlink()
        except OSError:
            pass
        result.status = "failed"
        result.reason = "internal malformed-record accounting mismatch"
        return result
    if config.errors.policy == "strict" and malformed:
        try:
            staging_spool.unlink()
        except OSError:
            pass
        result.status = "failed"
        result.spool_path = None
        result.reason = f"strict mode: {malformed} malformed input record(s)"
        return result
    result.status = "processed"
    result.spool_path = staging_spool
    if config.cache.enabled and key:
        payload = {
            "adapter": adapter.name,
            "adapter_version": adapter.version,
            "file_sha256": result.digest,
            "file_size": result.size,
            "format": result.format,
            "encoding": result.encoding,
            "records_extracted": result.records_extracted,
            "records_mapped": result.records_mapped,
            "mapping_rejected": result.mapping_rejected,
            "chunks_created": result.chunks_created,
            "source_records_split": result.source_records_split,
            "malformed_input": result.malformed_input,
            "encoding_replacements": result.encoding_replacements,
            "warnings": result.warnings,
            "issues": result.issues,
            "normalize_stats": result.normalize_stats,
        }
        with context.cache_lock:
            context.cache.put(key, staging_spool, payload)
    return result


def _iter_spool(result: FileResult) -> Iterator[dict[str, Any]]:
    if result.spool_path is None:
        return
    try:
        with result.spool_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    raise BuildError(f"spool file for {result.entry.relative_path} has a blank line")
                try:
                    event = strict_json_loads(line)
                except (json.JSONDecodeError, ValueError) as error:
                    raise BuildError(
                        f"spool file for {result.entry.relative_path} is corrupt at line {line_number}: {error}"
                    ) from error
                if not isinstance(event, dict) or event.get("event") not in ("draft", "reject", "malformed"):
                    raise BuildError(f"spool file for {result.entry.relative_path} has an unknown event")
                yield event
    except OSError as error:
        raise BuildError(f"cannot read spool file for {result.entry.relative_path}: {error}") from error


def _rejected_entry(
    *,
    record_id: str,
    source: Mapping[str, Any],
    rule: str,
    message: str,
    sample: str | None,
) -> dict[str, Any]:
    entry = {
        "record_id": record_id,
        "source": dict(source),
        "rule": rule,
        "message": message,
    }
    if sample is not None:
        entry["sample"] = sample
    return entry


def _publish(staging: Path, output: Path, quality_failed: bool, build_id: str) -> Path:
    if quality_failed:
        failed_root = output / "failed"
        try:
            failed_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise BuildError(f"cannot create failed-build directory {failed_root}: {error}") from error
        timestamp = utc_now_iso().replace(":", "").replace("-", "")
        destination = failed_root / f"{build_id}-{timestamp}"
        suffix = 1
        while destination.exists():
            suffix += 1
            destination = failed_root / f"{build_id}-{timestamp}-{suffix}"
        try:
            staging.rename(destination)
        except OSError as error:
            raise BuildError(f"cannot publish failed build to {destination}: {error}") from error
        return destination
    current = output / "current"
    previous = output / "previous"
    try:
        if previous.exists():
            shutil.rmtree(previous)
        if current.exists():
            current.rename(previous)
        try:
            staging.rename(current)
        except OSError:
            if previous.exists() and not current.exists():
                previous.rename(current)
            raise
    except OSError as error:
        raise BuildError(f"cannot publish build to {current}: {error}") from error
    return current


def _peak_rss() -> int | None:
    try:
        with open("/proc/self/status", "r", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF)
    except (AttributeError, OSError):
        return None
    return int(usage.ru_maxrss) * 1024


def _optional_dependencies() -> dict[str, str]:
    try:
        import importlib.metadata as metadata
    except ImportError:
        return {}
    result: dict[str, str] = {}
    for name in ("pypdf",):
        try:
            result[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return result


def validate_build_config(config: Config) -> LoadedSchema:
    if not config.source.path.exists():
        raise ConfigError(f"source.path does not exist: {config.source.path}")
    if not config.source.path.is_dir():
        raise ConfigError(f"source.path must be a directory: {config.source.path}")
    _validate_paths(config)
    schema = load_schema(config)
    if config.chunking.enabled and config.chunking.unit == "tokens":
        load_tokenizer(config.chunking.tokenizer)
    return schema


def run_build(
    config: Config,
    *,
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
) -> BuildResult:
    started_at = utc_now_iso()
    total_started = time.perf_counter()
    schema = validate_build_config(config)
    mapping = dict(schema.default_mapping())
    mapping.update(config.mapping.fields)
    tokenizer = None
    if config.chunking.enabled and config.chunking.unit == "tokens":
        tokenizer = load_tokenizer(config.chunking.tokenizer)
    output = config.output.directory.resolve()
    try:
        output.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise BuildError(f"cannot create output directory {output}: {error}") from error
    cancel = cancel_event or threading.Event()
    with _BuildLock(output / ".datum.lock"):
        _check_disk(config)
        discovery_started = time.perf_counter()
        discovered = discover(config.source)
        discovery_seconds = time.perf_counter() - discovery_started
        if cancel.is_set():
            raise BuildCancelled("build cancelled")
        staging = _stage_path(output, "staging")
        spool_dir = _stage_path(output, "spool")
        try:
            staging.mkdir(parents=True, exist_ok=False)
            spool_dir.mkdir(parents=True, exist_ok=False)
        except OSError as error:
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(spool_dir, ignore_errors=True)
            raise BuildError(f"cannot create build work directories: {error}") from error
        published = False
        writers: DatasetWriters | None = None
        dedup_index = None
        try:
            cache = FileCache(config, schema.spec)
            worker_context = _WorkerContext(
                config=config,
                schema=schema,
                mapping=mapping,
                cache=cache,
                tokenizer=tokenizer,
                tokenizer_lock=threading.Lock(),
                cache_lock=threading.Lock(),
                spool_dir=spool_dir,
                cancel_event=cancel,
            )
            files = discovered.files
            worker_count = resolve_workers(config, len(files), discovered.total_bytes)
            results: list[FileResult] = []
            file_counters = FileCounters(
                discovered=len(files),
                input_bytes_discovered=discovered.total_bytes,
                excluded_by_config=discovered.excluded,
                hidden_skipped=discovered.hidden,
                symlinks_skipped=discovered.symlinks_skipped,
                non_regular_skipped=discovered.non_regular_skipped,
                duplicate_targets_skipped=discovered.duplicate_targets_skipped,
                walk_issues=len(discovered.walk_issues),
            )
            errors = ErrorCollector()
            for issue in discovered.walk_issues:
                errors.add("walk_error", issue.message, path=issue.path)
            processing_started = time.perf_counter()
            pending: deque[Future[FileResult]] = deque()
            iterator = iter(enumerate(files))
            exhausted = False
            with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="datum") as pool:
                try:
                    while pending or not exhausted:
                        if cancel.is_set():
                            raise BuildCancelled("build cancelled")
                        while len(pending) < max(1, worker_count * 2) and not exhausted:
                            try:
                                index, entry = next(iterator)
                            except StopIteration:
                                exhausted = True
                                break
                            pending.append(pool.submit(_process_file, index, entry, worker_context))
                        if not pending:
                            break
                        future = pending.popleft()
                        result = future.result()
                        results.append(result)
                        if result.status == "processed":
                            file_counters.processed += 1
                            file_counters.input_bytes_processed += result.size
                            if result.malformed_input or result.mapping_rejected:
                                file_counters.processed_with_errors += 1
                            if result.records_extracted == 0:
                                file_counters.empty_files += 1
                        elif result.status == "skipped":
                            file_counters.skipped += 1
                            errors.add(
                                "unsupported_input",
                                result.reason or "unsupported input",
                                path=result.entry.relative_path,
                                severity="warning" if config.errors.unsupported == "skip" else "error",
                            )
                        else:
                            file_counters.failed += 1
                            errors.add(
                                "file_error",
                                result.reason or "file processing failed",
                                path=result.entry.relative_path,
                            )
                            if config.errors.policy == "strict":
                                raise BuildError(
                                    f"strict mode failed processing {result.entry.relative_path}: "
                                    f"{result.reason}"
                                )
                        if progress is not None:
                            progress(len(results), len(files), result)
                        if cancel.is_set():
                            raise BuildCancelled("build cancelled")
                except KeyboardInterrupt:
                    cancel.set()
                    for future in pending:
                        future.cancel()
                    raise BuildCancelled("build interrupted") from None
            processing_seconds = time.perf_counter() - processing_started
            if len(results) != len(files):
                raise BuildError("internal error: not all discovered files were processed")
            results.sort(key=lambda item: item.index)
            input_entries = [
                result.input_entry(config.manifest.include_input_hashes)
                for result in results
            ]
            build_id = build_id_for(config, schema.spec, input_entries)

            records = RecordCounters()
            transformations = TransformationCounters()
            lengths: list[int] = []
            rejected_by_rule: Counter[str] = Counter()
            sample_count = 0
            dedup_hash_seconds = 0.0
            dedup_index_seconds = 0.0
            compile_started = time.perf_counter()
            writers = DatasetWriters(staging, config)
            dedup_index = make_dedup_index(config.dedup, spool_dir)
            for result in results:
                if result.status != "processed":
                    continue
                if result.spool_path is None:
                    raise BuildError(
                        f"processed file {result.entry.relative_path} has no extraction spool"
                    )
                records.source_records_extracted += result.records_extracted
                records.source_records_mapped += result.records_mapped
                records.chunks_created += result.chunks_created
                records.source_records_split += result.source_records_split
                records.malformed_input += result.malformed_input
                transformations.encoding_replacements += result.encoding_replacements
                transformations.warnings += len(result.warnings)
                if result.normalize_stats:
                    stats = NormalizeStats(**result.normalize_stats)
                    transformations.add_normalization(stats)
                spool_drafts = 0
                spool_rejects = 0
                spool_malformed = 0
                for event in _iter_spool(result):
                    kind = event["event"]
                    source = event.get("source") or {}
                    if kind == "malformed":
                        spool_malformed += 1
                        issue = event.get("issue") or {}
                        line = issue.get("line")
                        ordinal = issue.get("ordinal") or 0
                        record_id = _record_id(
                            result.entry.relative_path,
                            result.digest,
                            int(ordinal) if ordinal else 0,
                            0,
                            f":malformed:{spool_malformed}",
                        )
                        message = str(issue.get("message") or "malformed input record")
                        code = str(issue.get("code") or "malformed_input")
                        writers.write_rejected(
                            _rejected_entry(
                                record_id=record_id,
                                source=source,
                                rule="malformed_input",
                                message=message,
                                sample=None,
                            )
                        )
                        rejected_by_rule["malformed_input"] += 1
                        errors.add(code, message, path=result.entry.relative_path, line=line)
                        continue
                    if kind == "reject":
                        spool_rejects += 1
                        records.mapping_rejected += 1
                        rule = str(event.get("rule") or "mapping_error")
                        message = str(event.get("message") or "record could not be mapped")
                        record_id = _record_id(
                            result.entry.relative_path,
                            result.digest,
                            int(source.get("ordinal") or 0),
                            0,
                            f":mapping:{source.get('reject_ordinal', spool_rejects)}",
                        )
                        writers.write_rejected(
                            _rejected_entry(
                                record_id=record_id,
                                source=source,
                                rule=rule,
                                message=message,
                                sample=None,
                            )
                        )
                        rejected_by_rule[rule] += 1
                        errors.add(rule, message, path=result.entry.relative_path)
                        continue
                    spool_drafts += 1
                    draft_raw = event.get("draft")
                    if not isinstance(draft_raw, dict):
                        raise BuildError(
                            f"spool for {result.entry.relative_path} contains a malformed draft"
                        )
                    draft = Draft.from_dict(draft_raw)
                    data = draft.data
                    length = draft.length
                    if config.length.oversize == "truncate" and length > config.length.max_chars:
                        text = data.get("text")
                        if not isinstance(text, str):
                            raise BuildError("internal error: truncation requires a text schema")
                        data = dict(data)
                        data["text"] = text[: config.length.max_chars]
                        length = len(data["text"])
                        draft = replace(
                            draft,
                            data=data,
                            length=length,
                            flags={**dict(draft.flags), "truncated": True},
                        )
                        records.truncated += 1
                        transformations.truncated_records += 1
                    if length == 0:
                        records.empty += 1
                        rule = "empty"
                        message = "record content is empty"
                    elif length < config.length.min_chars:
                        records.too_short += 1
                        rule = "too_short"
                        message = (
                            f"record length {length} is below length.min_chars "
                            f"{config.length.min_chars}"
                        )
                    elif length > config.length.max_chars:
                        records.too_long += 1
                        rule = "too_long"
                        message = (
                            f"record length {length} exceeds length.max_chars "
                            f"{config.length.max_chars}"
                        )
                    else:
                        problems = validate_record(data, schema.spec)
                        if problems:
                            records.schema_rejected += 1
                            rule = "schema_invalid"
                            message = "; ".join(problems[:5])
                        else:
                            rule = ""
                            message = ""
                    record_id = _record_id(
                        result.entry.relative_path,
                        result.digest,
                        int(source.get("ordinal") or 0),
                        draft.chunk_index,
                    )
                    if rule:
                        sample = None
                        if config.quality.include_samples and sample_count < config.quality.max_samples:
                            sample = _preview(data, config.quality.sample_chars)
                            sample_count += 1
                        writers.write_rejected(
                            _rejected_entry(
                                record_id=record_id,
                                source=source,
                                rule=rule,
                                message=message,
                                sample=sample,
                            )
                        )
                        rejected_by_rule[rule] += 1
                        if rule == "schema_invalid":
                            errors.add(
                                "schema_invalid",
                                message,
                                path=result.entry.relative_path,
                            )
                        continue
                    digest_started = time.perf_counter()
                    try:
                        from .dedup import draft_digest

                        content_digest = draft_digest(data)
                    except ValueError as error:
                        records.schema_rejected += 1
                        message = f"record cannot be serialized: {error}"
                        writers.write_rejected(
                            _rejected_entry(
                                record_id=record_id,
                                source=source,
                                rule="serialization_error",
                                message=message,
                                sample=None,
                            )
                        )
                        rejected_by_rule["serialization_error"] += 1
                        errors.add("serialization_error", message, path=result.entry.relative_path)
                        continue
                    dedup_hash_seconds += time.perf_counter() - digest_started
                    if config.dedup.enabled:
                        index_started = time.perf_counter()
                        is_new = dedup_index.add(content_digest)
                        dedup_index_seconds += time.perf_counter() - index_started
                        if not is_new:
                            records.duplicates += 1
                            sample = None
                            if config.quality.include_samples and sample_count < config.quality.max_samples:
                                sample = _preview(data, config.quality.sample_chars)
                                sample_count += 1
                            writers.write_rejected(
                                _rejected_entry(
                                    record_id=record_id,
                                    source=source,
                                    rule="duplicate",
                                    message="exact duplicate of an earlier record",
                                    sample=sample,
                                )
                            )
                            rejected_by_rule["duplicate"] += 1
                            continue
                    split = split_for(content_digest, config)
                    output_data = data
                    if config.provenance.mode in ("inline", "both"):
                        output_data = dict(data)
                        output_data[config.provenance.inline_field] = {
                            "record_id": record_id,
                            "source": {
                                "path": result.entry.relative_path,
                                "ordinal": source.get("ordinal"),
                                "locator": source.get("locator"),
                            },
                            **(
                                {"file_sha256": result.digest}
                                if config.provenance.include_file_sha256
                                else {}
                            ),
                        }
                    output_file, output_line = writers.write_dataset(split, output_data)
                    records.exported += 1
                    lengths.append(length)
                    if config.provenance.mode in ("sidecar", "both"):
                        provenance = {
                            "record_id": record_id,
                            "output_file": output_file,
                            "output_line": output_line,
                            "source": {
                                "path": result.entry.relative_path,
                                "ordinal": source.get("ordinal"),
                                "locator": source.get("locator"),
                                "chunk_index": draft.chunk_index,
                                "chunk_count": draft.chunk_count,
                                "char_start": draft.char_start,
                                "char_end": draft.char_end,
                                "token_start": draft.token_start,
                                "token_end": draft.token_end,
                            },
                            "schema": {"name": schema.spec.name, "version": schema.spec.version},
                            "adapter": result.adapter,
                            "format": result.format,
                            "truncated": bool(draft.flags.get("truncated")),
                            "source_records_split": result.source_records_split,
                        }
                        if config.provenance.include_file_sha256:
                            provenance["file_sha256"] = result.digest
                        writers.write_sidecar(provenance)
                if spool_drafts != result.chunks_created:
                    raise BuildError(
                        f"spool draft count mismatch for {result.entry.relative_path}: "
                        f"{spool_drafts} != {result.chunks_created}"
                    )
                if spool_rejects != result.mapping_rejected:
                    raise BuildError(
                        f"spool mapping-rejection mismatch for {result.entry.relative_path}"
                    )
                if spool_malformed != result.malformed_input:
                    raise BuildError(
                        f"spool malformed-input mismatch for {result.entry.relative_path}"
                    )
            compile_seconds = time.perf_counter() - compile_started
            if dedup_index is not None:
                dedup_index.close()
                dedup_index = None
            export_stats = writers.finish()
            writers = None
            outputs: list[dict[str, Any]] = []
            dataset_hashes: dict[str, str] = {}
            for split, count in sorted(export_stats.counts.items()):
                filename = {
                    "dataset": "dataset.jsonl",
                    "train": "train.jsonl",
                    "validation": "validation.jsonl",
                    "test": "test.jsonl",
                }[split]
                path = staging / filename
                digest, size = sha256_file(path)
                dataset_hashes[filename] = digest
                outputs.append(
                    {"path": filename, "sha256": digest, "bytes": size, "records": count}
                )
            sidecar_entry = None
            if config.provenance.mode in ("sidecar", "both"):
                sidecar_path = staging / SIDECAR_FILENAME
                sidecar_digest, sidecar_size = sha256_file(sidecar_path)
                sidecar_entry = {
                    "path": SIDECAR_FILENAME,
                    "sha256": sidecar_digest,
                    "bytes": sidecar_size,
                    "records": export_stats.sidecar_lines,
                }
            rejected_path = staging / "rejected.jsonl"
            rejected_digest, rejected_size = sha256_file(rejected_path)
            rejected_entry = {
                "path": "rejected.jsonl",
                "sha256": rejected_digest,
                "bytes": rejected_size,
                "records": export_stats.rejected_lines,
            }
            elapsed_before_verify = time.perf_counter() - total_started
            performance = {
                "total_seconds": round(elapsed_before_verify, 6),
                "discovery_seconds": round(discovery_seconds, 6),
                "processing_seconds": round(processing_seconds, 6),
                "compile_seconds": round(compile_seconds, 6),
                "dedup_hash_seconds": round(dedup_hash_seconds, 6),
                "dedup_index_seconds": round(dedup_index_seconds, 6),
                "verification_seconds": 0.0,
                "workers": worker_count,
                "files_per_second": round(
                    len(results) / processing_seconds, 6
                ) if processing_seconds > 0 else None,
                "source_records_per_second": round(
                    records.source_records_extracted / processing_seconds, 6
                ) if processing_seconds > 0 else None,
                "exported_records_per_second": round(
                    records.exported / elapsed_before_verify, 6
                ) if elapsed_before_verify > 0 else None,
                "input_bytes": file_counters.input_bytes_processed,
                "output_bytes": export_stats.output_bytes,
                "sidecar_bytes": export_stats.sidecar_bytes,
                "rejected_bytes": export_stats.rejected_bytes,
                "input_mib_per_second": round(
                    file_counters.input_bytes_processed / (1024 * 1024) / processing_seconds, 6
                ) if processing_seconds > 0 else None,
                "peak_rss_bytes": _peak_rss(),
            }
            quality = build_quality_summary(
                build_id=build_id,
                files=file_counters,
                records=records,
                schema_name=schema.spec.name,
                schema_version=schema.spec.version,
                lengths=lengths,
                split_counts=export_stats.counts,
                transformations=transformations,
                performance=performance,
                cache=cache.summary(),
                errors=errors,
                rejected_by_rule=rejected_by_rule,
                quality_options=config.quality,
                rejected_log_entries=export_stats.rejected_lines,
            )
            if quality.invariant_failures:
                raise BuildError(
                    "critical quality invariants failed: " + ", ".join(quality.invariant_failures)
                )
            finished_at = utc_now_iso()
            manifest = build_manifest(
                config=config,
                schema=schema.spec,
                build_id=build_id,
                status="complete" if quality.status == "passed" else "quality_failed",
                started_at=started_at,
                finished_at=finished_at,
                inputs=input_entries,
                outputs=outputs,
                sidecar=sidecar_entry,
                rejected=rejected_entry,
                quality_summary=quality.report,
                cache_summary=cache.summary(),
                performance=performance,
                environment=environment_info(),
                optional_dependencies=_optional_dependencies(),
            )
            atomic_write_json(staging / "config.resolved.json", config.to_dict())
            atomic_write_json(staging / "quality.json", quality.report)
            atomic_write_text(staging / "quality.txt", quality.text)
            write_manifest(staging / "manifest.json", manifest)
            verify_started = time.perf_counter()
            report = verify_build(staging)
            verification_seconds = time.perf_counter() - verify_started
            if not report.ok:
                raise BuildError(
                    "post-export verification failed: " + "; ".join(report.errors[:5])
                )
            total_after_verify = time.perf_counter() - total_started
            performance["verification_seconds"] = round(verification_seconds, 6)
            performance["total_seconds"] = round(total_after_verify, 6)
            finished_at = utc_now_iso()
            quality = build_quality_summary(
                build_id=build_id,
                files=file_counters,
                records=records,
                schema_name=schema.spec.name,
                schema_version=schema.spec.version,
                lengths=lengths,
                split_counts=export_stats.counts,
                transformations=transformations,
                performance=performance,
                cache=cache.summary(),
                errors=errors,
                rejected_by_rule=rejected_by_rule,
                quality_options=config.quality,
                rejected_log_entries=export_stats.rejected_lines,
            )
            if quality.invariant_failures:
                raise BuildError(
                    "critical quality invariants failed: " + ", ".join(quality.invariant_failures)
                )
            manifest = build_manifest(
                config=config,
                schema=schema.spec,
                build_id=build_id,
                status="complete" if quality.status == "passed" else "quality_failed",
                started_at=started_at,
                finished_at=finished_at,
                inputs=input_entries,
                outputs=outputs,
                sidecar=sidecar_entry,
                rejected=rejected_entry,
                quality_summary=quality.report,
                cache_summary=cache.summary(),
                performance=performance,
                environment=environment_info(),
                optional_dependencies=_optional_dependencies(),
            )
            atomic_write_json(staging / "quality.json", quality.report)
            atomic_write_text(staging / "quality.txt", quality.text)
            write_manifest(staging / "manifest.json", manifest)
            public_path = _publish(
                staging,
                output,
                quality.status == "quality_failed",
                build_id,
            )
            published = True
            return BuildResult(
                build_id=build_id,
                status=quality.status,
                output_directory=public_path,
                manifest_path=public_path / "manifest.json",
                quality_path=public_path / "quality.json",
                manifest=manifest,
                quality=quality.report,
                files=quality.report["files"],
                records=quality.report["records"],
                splits=dict(sorted(export_stats.counts.items())),
                cache=cache.summary(),
                performance=performance,
                verification_seconds=verification_seconds,
            )
        except BuildCancelled:
            raise
        except Exception:
            if writers is not None:
                writers.abort()
            if dedup_index is not None:
                try:
                    dedup_index.close()
                except Exception:
                    pass
            raise
        finally:
            shutil.rmtree(spool_dir, ignore_errors=True)
            if not published:
                shutil.rmtree(staging, ignore_errors=True)


def build_summary_text(result: BuildResult) -> str:
    status = "PASS" if result.status == "passed" else "QUALITY FAILED"
    lines = [
        f"DATUM build {status}",
        f"Build ID: {result.build_id}",
        f"Output: {result.output_directory}",
        f"Files: {result.files.get('processed', 0)} processed, "
        f"{result.files.get('failed', 0)} failed, {result.files.get('skipped', 0)} skipped",
        f"Records: {result.records.get('exported', 0)} exported from "
        f"{result.records.get('source_records_extracted', 0)} source records",
        "Splits: " + ", ".join(f"{name}={count}" for name, count in result.splits.items()),
        f"Cache: {result.cache.get('hits', 0)} hits, {result.cache.get('misses', 0)} misses",
        f"Time: {human_duration(float(result.performance.get('total_seconds', 0.0)))} "
        f"(verification {human_duration(result.verification_seconds)})",
        f"Manifest: {result.manifest_path}",
    ]
    return "\n".join(lines)
