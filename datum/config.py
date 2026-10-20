"""TOML configuration loading, validation, and canonical fingerprints."""

from __future__ import annotations

import codecs
import hashlib
import math
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from .errors import ConfigError
from .util import canonical_json

BUILTIN_SCHEMAS = ("text", "completion", "instruction", "chat", "qa", "classification", "custom")


@dataclass(frozen=True)
class SourceOptions:
    path: Path
    recursive: bool = True
    include_hidden: bool = False
    exclude: tuple[str, ...] = ()
    follow_symlinks: bool = False
    encoding: str = "auto"
    encoding_errors: str = "strict"
    detect_content: bool = True
    text_fallback: bool = False
    ordering: str = "path"
    csv_delimiter: str = "auto"
    csv_has_header: bool = True
    json_records_path: str = ""
    jsonl_skip_blank: bool = True


@dataclass(frozen=True)
class LimitsOptions:
    max_file_bytes: int = 64 * 1024 * 1024
    max_records_per_file: int = 1_000_000
    max_archive_uncompressed_bytes: int = 256 * 1024 * 1024
    min_free_disk_bytes: int = 50 * 1024 * 1024


@dataclass(frozen=True)
class NormalizeOptions:
    line_endings: str = "lf"
    unicode_form: str = "none"
    strip_outer_whitespace: bool = True
    strip_control_chars: bool = True
    collapse_whitespace: bool = False
    strip_trailing_whitespace: bool = False


@dataclass(frozen=True)
class ChunkingOptions:
    enabled: bool = False
    unit: str = "chars"
    size: int = 4000
    overlap: int = 0
    tokenizer: str = ""


@dataclass(frozen=True)
class LengthOptions:
    min_chars: int = 1
    max_chars: int = 1_000_000
    oversize: str = "reject"


@dataclass(frozen=True)
class SchemaOptions:
    name: str = "text"
    version: int = 1
    file: Path | None = None


@dataclass(frozen=True)
class MappingOptions:
    fields: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DedupOptions:
    enabled: bool = True
    index: str = "memory"


@dataclass(frozen=True)
class SplitOptions:
    enabled: bool = False
    seed: int = 42
    train: float = 0.9
    validation: float = 0.05
    test: float = 0.05


@dataclass(frozen=True)
class ProvenanceOptions:
    mode: str = "sidecar"
    inline_field: str = "_datum"
    include_file_sha256: bool = True


@dataclass(frozen=True)
class OutputOptions:
    directory: Path


@dataclass(frozen=True)
class QualityOptions:
    include_samples: bool = False
    sample_chars: int = 160
    max_samples: int = 20
    min_exported_records: int = 1
    max_file_error_rate: float = 1.0
    max_schema_rejection_rate: float = 1.0
    max_duplicate_rate: float = 1.0


@dataclass(frozen=True)
class ErrorOptions:
    policy: str = "permissive"
    unsupported: str = "skip"


@dataclass(frozen=True)
class CacheOptions:
    enabled: bool = True
    directory: Path | None = None
    verify_integrity: bool = True


@dataclass(frozen=True)
class RuntimeOptions:
    workers: int = 0
    progress: bool = True


@dataclass(frozen=True)
class PDFOptions:
    engine: str = "builtin"


@dataclass(frozen=True)
class AIOptions:
    enabled: bool = False
    provider: str = ""
    model: str = ""
    max_calls: int = 0
    api_key_env: str = ""
    base_url: str = ""


@dataclass(frozen=True)
class ManifestOptions:
    include_input_hashes: bool = True


@dataclass(frozen=True)
class Config:
    path: Path
    config_version: int
    source: SourceOptions
    limits: LimitsOptions
    normalize: NormalizeOptions
    chunking: ChunkingOptions
    length: LengthOptions
    schema: SchemaOptions
    mapping: MappingOptions
    dedup: DedupOptions
    split: SplitOptions
    provenance: ProvenanceOptions
    output: OutputOptions
    quality: QualityOptions
    errors: ErrorOptions
    cache: CacheOptions
    runtime: RuntimeOptions
    pdf: PDFOptions
    ai: AIOptions
    manifest: ManifestOptions
    completion_separator: str = ""

    @property
    def base_dir(self) -> Path:
        return self.path.parent

    def with_overrides(
        self,
        *,
        output: Path | None = None,
        workers: int | None = None,
        strict: bool | None = None,
        cache_enabled: bool | None = None,
        cache_dir: Path | None = None,
        progress: bool | None = None,
    ) -> "Config":
        output_options = self.output
        if output is not None:
            output_options = OutputOptions(directory=output.expanduser().resolve())
        runtime = self.runtime
        if workers is not None or progress is not None:
            runtime = replace(
                runtime,
                workers=self.runtime.workers if workers is None else workers,
                progress=self.runtime.progress if progress is None else progress,
            )
        errors = self.errors
        if strict is not None:
            errors = replace(errors, policy="strict" if strict else "permissive")
        cache = self.cache
        if cache_enabled is not None or cache_dir is not None:
            cache = replace(
                cache,
                enabled=cache.enabled if cache_enabled is None else cache_enabled,
                directory=cache.directory if cache_dir is None else cache_dir.expanduser().resolve(),
            )
        return replace(
            self,
            output=output_options,
            runtime=runtime,
            errors=errors,
            cache=cache,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_version": self.config_version,
            "source": {
                "path": str(self.source.path),
                "recursive": self.source.recursive,
                "include_hidden": self.source.include_hidden,
                "exclude": list(self.source.exclude),
                "follow_symlinks": self.source.follow_symlinks,
                "encoding": self.source.encoding,
                "encoding_errors": self.source.encoding_errors,
                "detect_content": self.source.detect_content,
                "text_fallback": self.source.text_fallback,
                "ordering": self.source.ordering,
                "csv_delimiter": self.source.csv_delimiter,
                "csv_has_header": self.source.csv_has_header,
                "json_records_path": self.source.json_records_path,
                "jsonl_skip_blank": self.source.jsonl_skip_blank,
            },
            "limits": {
                "max_file_bytes": self.limits.max_file_bytes,
                "max_records_per_file": self.limits.max_records_per_file,
                "max_archive_uncompressed_bytes": self.limits.max_archive_uncompressed_bytes,
                "min_free_disk_bytes": self.limits.min_free_disk_bytes,
            },
            "normalize": {
                "line_endings": self.normalize.line_endings,
                "unicode_form": self.normalize.unicode_form,
                "strip_outer_whitespace": self.normalize.strip_outer_whitespace,
                "strip_control_chars": self.normalize.strip_control_chars,
                "collapse_whitespace": self.normalize.collapse_whitespace,
                "strip_trailing_whitespace": self.normalize.strip_trailing_whitespace,
            },
            "chunking": {
                "enabled": self.chunking.enabled,
                "unit": self.chunking.unit,
                "size": self.chunking.size,
                "overlap": self.chunking.overlap,
                "tokenizer": self.chunking.tokenizer,
            },
            "length": {
                "min_chars": self.length.min_chars,
                "max_chars": self.length.max_chars,
                "oversize": self.length.oversize,
            },
            "schema": {
                "name": self.schema.name,
                "version": self.schema.version,
                "file": None if self.schema.file is None else str(self.schema.file),
            },
            "mapping": {"fields": dict(sorted(self.mapping.fields.items()))},
            "completion_separator": self.completion_separator,
            "dedup": {"enabled": self.dedup.enabled, "index": self.dedup.index},
            "split": {
                "enabled": self.split.enabled,
                "seed": self.split.seed,
                "train": self.split.train,
                "validation": self.split.validation,
                "test": self.split.test,
            },
            "provenance": {
                "mode": self.provenance.mode,
                "inline_field": self.provenance.inline_field,
                "include_file_sha256": self.provenance.include_file_sha256,
            },
            "output": {"dir": str(self.output.directory)},
            "quality": {
                "include_samples": self.quality.include_samples,
                "sample_chars": self.quality.sample_chars,
                "max_samples": self.quality.max_samples,
                "min_exported_records": self.quality.min_exported_records,
                "max_file_error_rate": self.quality.max_file_error_rate,
                "max_schema_rejection_rate": self.quality.max_schema_rejection_rate,
                "max_duplicate_rate": self.quality.max_duplicate_rate,
            },
            "errors": {"policy": self.errors.policy, "unsupported": self.errors.unsupported},
            "cache": {
                "enabled": self.cache.enabled,
                "dir": None if self.cache.directory is None else str(self.cache.directory),
                "verify_integrity": self.cache.verify_integrity,
            },
            "runtime": {"workers": self.runtime.workers, "progress": self.runtime.progress},
            "pdf": {"engine": self.pdf.engine},
            "ai": {
                "enabled": self.ai.enabled,
                "provider": self.ai.provider,
                "model": self.ai.model,
                "max_calls": self.ai.max_calls,
                "api_key_env": self.ai.api_key_env,
                "base_url": self.ai.base_url,
            },
            "manifest": {"include_input_hashes": self.manifest.include_input_hashes},
        }

    def identity_dict(self) -> dict[str, Any]:
        data = self.to_dict()
        data.pop("runtime")
        data.pop("quality")
        data.pop("cache")
        data.pop("manifest")
        data["output"] = {"files": "fixed"}
        data["source"] = dict(data["source"])
        data["source"].pop("path")
        return data

    def pipeline_dict(self) -> dict[str, Any]:
        data = self.to_dict()
        return {
            "source": data["source"],
            "normalize": data["normalize"],
            "chunking": data["chunking"],
            "length": data["length"],
            "schema": data["schema"],
            "mapping": data["mapping"],
            "completion_separator": data["completion_separator"],
            "pdf": data["pdf"],
            "limits": {
                "max_archive_uncompressed_bytes": self.limits.max_archive_uncompressed_bytes,
            },
        }

    def config_fingerprint(self) -> str:
        return hashlib.sha256(canonical_json(self.identity_dict()).encode("utf-8")).hexdigest()

    def pipeline_fingerprint(self) -> str:
        return hashlib.sha256(canonical_json(self.pipeline_dict()).encode("utf-8")).hexdigest()

    def cache_directory(self) -> Path:
        return self.cache.directory if self.cache.directory is not None else self.output.directory / ".datum-cache"


def _expect_table(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{where} must be a TOML table")
    return value


def _check_unknown(table: Mapping[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        rendered = ", ".join(unknown)
        raise ConfigError(f"unknown key(s) in {where}: {rendered}; allowed keys: {', '.join(sorted(allowed))}")


def _string(table: Mapping[str, Any], key: str, default: str, where: str) -> str:
    value = table.get(key, default)
    if not isinstance(value, str):
        raise ConfigError(f"{where}.{key} must be a string")
    return value


def _boolean(table: Mapping[str, Any], key: str, default: bool, where: str) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{where}.{key} must be a boolean")
    return value


def _integer(table: Mapping[str, Any], key: str, default: int, where: str) -> int:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where}.{key} must be an integer")
    return value


def _number(table: Mapping[str, Any], key: str, default: float, where: str) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where}.{key} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ConfigError(f"{where}.{key} must be finite")
    return number


def _string_list(table: Mapping[str, Any], key: str, default: tuple[str, ...], where: str) -> tuple[str, ...]:
    value = table.get(key, list(default))
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"{where}.{key} must be an array of strings")
    return tuple(value)


def _resolve_path(base: Path, value: str, where: str) -> Path:
    if not value.strip():
        raise ConfigError(f"{where} must not be empty")
    expanded = Path(value).expanduser()
    if not expanded.is_absolute():
        expanded = base / expanded
    return expanded.resolve()


def _validate_encoding(value: str) -> None:
    if value in ("auto", ""):
        return
    try:
        codecs.lookup(value)
    except LookupError as error:
        raise ConfigError(f"source.encoding is not a recognised codec: {value}") from error


def _load_schema_file_option(base: Path, table: Mapping[str, Any]) -> Path | None:
    raw = _string(table, "file", "", "schema")
    if raw:
        return _resolve_path(base, raw, "schema.file")
    return None


def load_config(path: str | Path, *, allow_missing_source: bool = True) -> Config:
    config_path = Path(path).expanduser().resolve()
    try:
        with config_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError as error:
        raise ConfigError(f"configuration file not found: {config_path}") from error
    except OSError as error:
        raise ConfigError(f"cannot read configuration file {config_path}: {error}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"invalid TOML in {config_path}: {error}") from error

    base = config_path.parent
    root = _expect_table(raw, "configuration")
    _check_unknown(
        root,
        {
            "datum",
            "source",
            "limits",
            "normalize",
            "chunking",
            "length",
            "schema",
            "mapping",
            "dedup",
            "split",
            "provenance",
            "output",
            "quality",
            "errors",
            "cache",
            "runtime",
            "pdf",
            "ai",
            "manifest",
            "completion_separator",
        },
        "configuration",
    )

    datum_table = _expect_table(root.get("datum", {}), "datum")
    _check_unknown(datum_table, {"config_version"}, "datum")
    config_version = _integer(datum_table, "config_version", 1, "datum")
    if config_version != 1:
        raise ConfigError(f"unsupported config_version {config_version}; this DATUM release supports 1")

    source_table = _expect_table(root.get("source", {}), "source")
    _check_unknown(
        source_table,
        {
            "path",
            "recursive",
            "include_hidden",
            "exclude",
            "follow_symlinks",
            "encoding",
            "encoding_errors",
            "detect_content",
            "text_fallback",
            "ordering",
            "csv_delimiter",
            "csv_has_header",
            "json_records_path",
            "jsonl_skip_blank",
        },
        "source",
    )
    source_path_raw = _string(source_table, "path", "", "source")
    if not source_path_raw:
        raise ConfigError("source.path is required")
    source_path = _resolve_path(base, source_path_raw, "source.path")
    if not allow_missing_source and not source_path.exists():
        raise ConfigError(f"source.path does not exist: {source_path}")
    if not allow_missing_source and not source_path.is_dir():
        raise ConfigError(f"source.path must be a directory: {source_path}")
    source = SourceOptions(
        path=source_path,
        recursive=_boolean(source_table, "recursive", True, "source"),
        include_hidden=_boolean(source_table, "include_hidden", False, "source"),
        exclude=_string_list(source_table, "exclude", (), "source"),
        follow_symlinks=_boolean(source_table, "follow_symlinks", False, "source"),
        encoding=_string(source_table, "encoding", "auto", "source"),
        encoding_errors=_string(source_table, "encoding_errors", "strict", "source"),
        detect_content=_boolean(source_table, "detect_content", True, "source"),
        text_fallback=_boolean(source_table, "text_fallback", False, "source"),
        ordering=_string(source_table, "ordering", "path", "source"),
        csv_delimiter=_string(source_table, "csv_delimiter", "auto", "source"),
        csv_has_header=_boolean(source_table, "csv_has_header", True, "source"),
        json_records_path=_string(source_table, "json_records_path", "", "source"),
        jsonl_skip_blank=_boolean(source_table, "jsonl_skip_blank", True, "source"),
    )
    _validate_encoding(source.encoding)
    if source.encoding_errors not in ("strict", "replace"):
        raise ConfigError("source.encoding_errors must be 'strict' or 'replace'")
    if source.ordering != "path":
        raise ConfigError("source.ordering must be 'path' in DATUM 0.1.0")
    if source.csv_delimiter not in ("auto", "comma", "tab", "semicolon", "pipe") and len(source.csv_delimiter) != 1:
        raise ConfigError("source.csv_delimiter must be auto, comma, tab, semicolon, pipe, or a single character")

    limits_table = _expect_table(root.get("limits", {}), "limits")
    _check_unknown(
        limits_table,
        {
            "max_file_bytes",
            "max_records_per_file",
            "max_archive_uncompressed_bytes",
            "min_free_disk_bytes",
        },
        "limits",
    )
    limits = LimitsOptions(
        max_file_bytes=_integer(limits_table, "max_file_bytes", 64 * 1024 * 1024, "limits"),
        max_records_per_file=_integer(limits_table, "max_records_per_file", 1_000_000, "limits"),
        max_archive_uncompressed_bytes=_integer(
            limits_table, "max_archive_uncompressed_bytes", 256 * 1024 * 1024, "limits"
        ),
        min_free_disk_bytes=_integer(limits_table, "min_free_disk_bytes", 50 * 1024 * 1024, "limits"),
    )
    if limits.max_file_bytes < 1:
        raise ConfigError("limits.max_file_bytes must be at least 1")
    if limits.max_records_per_file < 1:
        raise ConfigError("limits.max_records_per_file must be at least 1")
    if limits.max_archive_uncompressed_bytes < 1:
        raise ConfigError("limits.max_archive_uncompressed_bytes must be at least 1")
    if limits.min_free_disk_bytes < 0:
        raise ConfigError("limits.min_free_disk_bytes must not be negative")

    normalize_table = _expect_table(root.get("normalize", {}), "normalize")
    _check_unknown(
        normalize_table,
        {
            "line_endings",
            "unicode_form",
            "strip_outer_whitespace",
            "strip_control_chars",
            "collapse_whitespace",
            "strip_trailing_whitespace",
        },
        "normalize",
    )
    normalize = NormalizeOptions(
        line_endings=_string(normalize_table, "line_endings", "lf", "normalize"),
        unicode_form=_string(normalize_table, "unicode_form", "none", "normalize"),
        strip_outer_whitespace=_boolean(normalize_table, "strip_outer_whitespace", True, "normalize"),
        strip_control_chars=_boolean(normalize_table, "strip_control_chars", True, "normalize"),
        collapse_whitespace=_boolean(normalize_table, "collapse_whitespace", False, "normalize"),
        strip_trailing_whitespace=_boolean(normalize_table, "strip_trailing_whitespace", False, "normalize"),
    )
    if normalize.line_endings not in ("lf", "preserve"):
        raise ConfigError("normalize.line_endings must be 'lf' or 'preserve'")
    if normalize.unicode_form not in ("none", "NFC", "NFKC"):
        raise ConfigError("normalize.unicode_form must be none, NFC, or NFKC")

    chunking_table = _expect_table(root.get("chunking", {}), "chunking")
    _check_unknown(chunking_table, {"enabled", "unit", "size", "overlap", "tokenizer"}, "chunking")
    chunking = ChunkingOptions(
        enabled=_boolean(chunking_table, "enabled", False, "chunking"),
        unit=_string(chunking_table, "unit", "chars", "chunking"),
        size=_integer(chunking_table, "size", 4000, "chunking"),
        overlap=_integer(chunking_table, "overlap", 0, "chunking"),
        tokenizer=_string(chunking_table, "tokenizer", "", "chunking"),
    )
    if chunking.unit not in ("chars", "tokens"):
        raise ConfigError("chunking.unit must be 'chars' or 'tokens'")
    if chunking.size < 1:
        raise ConfigError("chunking.size must be at least 1")
    if chunking.overlap < 0:
        raise ConfigError("chunking.overlap must not be negative")
    if chunking.overlap >= chunking.size:
        raise ConfigError("chunking.overlap must be smaller than chunking.size")
    if chunking.enabled and chunking.unit == "tokens" and not chunking.tokenizer:
        raise ConfigError("chunking.tokenizer is required when chunking.unit is 'tokens'")

    length_table = _expect_table(root.get("length", {}), "length")
    _check_unknown(length_table, {"min_chars", "max_chars", "oversize"}, "length")
    length = LengthOptions(
        min_chars=_integer(length_table, "min_chars", 1, "length"),
        max_chars=_integer(length_table, "max_chars", 1_000_000, "length"),
        oversize=_string(length_table, "oversize", "reject", "length"),
    )
    if length.min_chars < 0:
        raise ConfigError("length.min_chars must not be negative")
    if length.max_chars < 1:
        raise ConfigError("length.max_chars must be at least 1")
    if length.min_chars > length.max_chars:
        raise ConfigError("length.min_chars must not exceed length.max_chars")
    if length.oversize not in ("reject", "truncate"):
        raise ConfigError("length.oversize must be 'reject' or 'truncate'")

    schema_table = _expect_table(root.get("schema", {}), "schema")
    _check_unknown(schema_table, {"name", "version", "file"}, "schema")
    schema_name = _string(schema_table, "name", "text", "schema")
    schema_version = _integer(schema_table, "version", 1, "schema")
    schema_file = _load_schema_file_option(base, schema_table)
    if schema_name not in BUILTIN_SCHEMAS:
        raise ConfigError(f"schema.name must be one of: {', '.join(BUILTIN_SCHEMAS)}")
    if schema_version < 1:
        raise ConfigError("schema.version must be at least 1")
    if schema_name == "custom" and schema_file is None:
        raise ConfigError("schema.file is required when schema.name is 'custom'")
    if schema_name != "custom" and schema_file is not None:
        raise ConfigError("schema.file is only valid with schema.name = 'custom'")
    schema = SchemaOptions(name=schema_name, version=schema_version, file=schema_file)

    mapping_table = _expect_table(root.get("mapping", {}), "mapping")
    _check_unknown(mapping_table, {"fields"}, "mapping")
    fields_raw = mapping_table.get("fields", {})
    if not isinstance(fields_raw, dict):
        raise ConfigError("mapping.fields must be a table of output_field = source.path")
    mapping_fields: dict[str, str] = {}
    for key, value in fields_raw.items():
        if not isinstance(key, str) or not key:
            raise ConfigError("mapping.fields keys must be non-empty strings")
        if not isinstance(value, str) or not value:
            raise ConfigError(f"mapping.fields.{key} must be a non-empty string path")
        mapping_fields[key] = value
    mapping = MappingOptions(fields=mapping_fields)

    dedup_table = _expect_table(root.get("dedup", {}), "dedup")
    _check_unknown(dedup_table, {"enabled", "index"}, "dedup")
    dedup = DedupOptions(
        enabled=_boolean(dedup_table, "enabled", True, "dedup"),
        index=_string(dedup_table, "index", "memory", "dedup"),
    )
    if dedup.index not in ("memory", "sqlite"):
        raise ConfigError("dedup.index must be 'memory' or 'sqlite'")

    split_table = _expect_table(root.get("split", {}), "split")
    _check_unknown(split_table, {"enabled", "seed", "train", "validation", "test"}, "split")
    split = SplitOptions(
        enabled=_boolean(split_table, "enabled", False, "split"),
        seed=_integer(split_table, "seed", 42, "split"),
        train=_number(split_table, "train", 0.9, "split"),
        validation=_number(split_table, "validation", 0.05, "split"),
        test=_number(split_table, "test", 0.05, "split"),
    )
    if split.seed < 0:
        raise ConfigError("split.seed must not be negative")
    for key, value in (("train", split.train), ("validation", split.validation), ("test", split.test)):
        if value < 0 or value > 1:
            raise ConfigError(f"split.{key} must be between 0 and 1")
    if not math.isclose(split.train + split.validation + split.test, 1.0, abs_tol=1e-9):
        raise ConfigError("split.train + split.validation + split.test must equal 1.0")
    if split.train + split.validation + split.test <= 0:
        raise ConfigError("at least one split ratio must be greater than zero")

    provenance_table = _expect_table(root.get("provenance", {}), "provenance")
    _check_unknown(provenance_table, {"mode", "inline_field", "include_file_sha256"}, "provenance")
    provenance = ProvenanceOptions(
        mode=_string(provenance_table, "mode", "sidecar", "provenance"),
        inline_field=_string(provenance_table, "inline_field", "_datum", "provenance"),
        include_file_sha256=_boolean(provenance_table, "include_file_sha256", True, "provenance"),
    )
    if provenance.mode not in ("sidecar", "inline", "both", "off"):
        raise ConfigError("provenance.mode must be sidecar, inline, both, or off")
    if not provenance.inline_field or any(
        not (character.isalnum() or character == "_") for character in provenance.inline_field
    ):
        raise ConfigError("provenance.inline_field must contain only letters, digits, and underscores")

    output_table = _expect_table(root.get("output", {}), "output")
    _check_unknown(output_table, {"dir"}, "output")
    output_raw = _string(output_table, "dir", "datum-output", "output")
    output = OutputOptions(directory=_resolve_path(base, output_raw, "output.dir"))

    quality_table = _expect_table(root.get("quality", {}), "quality")
    _check_unknown(
        quality_table,
        {
            "include_samples",
            "sample_chars",
            "max_samples",
            "min_exported_records",
            "max_file_error_rate",
            "max_schema_rejection_rate",
            "max_duplicate_rate",
        },
        "quality",
    )
    quality = QualityOptions(
        include_samples=_boolean(quality_table, "include_samples", False, "quality"),
        sample_chars=_integer(quality_table, "sample_chars", 160, "quality"),
        max_samples=_integer(quality_table, "max_samples", 20, "quality"),
        min_exported_records=_integer(quality_table, "min_exported_records", 1, "quality"),
        max_file_error_rate=_number(quality_table, "max_file_error_rate", 1.0, "quality"),
        max_schema_rejection_rate=_number(quality_table, "max_schema_rejection_rate", 1.0, "quality"),
        max_duplicate_rate=_number(quality_table, "max_duplicate_rate", 1.0, "quality"),
    )
    if quality.sample_chars < 1:
        raise ConfigError("quality.sample_chars must be at least 1")
    if quality.max_samples < 0:
        raise ConfigError("quality.max_samples must not be negative")
    if quality.min_exported_records < 0:
        raise ConfigError("quality.min_exported_records must not be negative")
    for key, value in (
        ("max_file_error_rate", quality.max_file_error_rate),
        ("max_schema_rejection_rate", quality.max_schema_rejection_rate),
        ("max_duplicate_rate", quality.max_duplicate_rate),
    ):
        if value < 0 or value > 1:
            raise ConfigError(f"quality.{key} must be between 0 and 1")

    errors_table = _expect_table(root.get("errors", {}), "errors")
    _check_unknown(errors_table, {"policy", "unsupported"}, "errors")
    errors = ErrorOptions(
        policy=_string(errors_table, "policy", "permissive", "errors"),
        unsupported=_string(errors_table, "unsupported", "skip", "errors"),
    )
    if errors.policy not in ("permissive", "strict"):
        raise ConfigError("errors.policy must be 'permissive' or 'strict'")
    if errors.unsupported not in ("skip", "fail"):
        raise ConfigError("errors.unsupported must be 'skip' or 'fail'")

    cache_table = _expect_table(root.get("cache", {}), "cache")
    _check_unknown(cache_table, {"enabled", "dir", "verify_integrity"}, "cache")
    cache_dir_raw = _string(cache_table, "dir", "", "cache")
    cache = CacheOptions(
        enabled=_boolean(cache_table, "enabled", True, "cache"),
        directory=_resolve_path(base, cache_dir_raw, "cache.dir") if cache_dir_raw else None,
        verify_integrity=_boolean(cache_table, "verify_integrity", True, "cache"),
    )

    runtime_table = _expect_table(root.get("runtime", {}), "runtime")
    _check_unknown(runtime_table, {"workers", "progress"}, "runtime")
    runtime = RuntimeOptions(
        workers=_integer(runtime_table, "workers", 0, "runtime"),
        progress=_boolean(runtime_table, "progress", True, "runtime"),
    )
    if runtime.workers < 0:
        raise ConfigError("runtime.workers must be zero (automatic) or positive")

    pdf_table = _expect_table(root.get("pdf", {}), "pdf")
    _check_unknown(pdf_table, {"engine"}, "pdf")
    pdf = PDFOptions(engine=_string(pdf_table, "engine", "builtin", "pdf"))
    if pdf.engine not in ("builtin", "pypdf", "auto"):
        raise ConfigError("pdf.engine must be builtin, pypdf, or auto")

    ai_table = _expect_table(root.get("ai", {}), "ai")
    _check_unknown(ai_table, {"enabled", "provider", "model", "max_calls", "api_key_env", "base_url"}, "ai")
    ai = AIOptions(
        enabled=_boolean(ai_table, "enabled", False, "ai"),
        provider=_string(ai_table, "provider", "", "ai"),
        model=_string(ai_table, "model", "", "ai"),
        max_calls=_integer(ai_table, "max_calls", 0, "ai"),
        api_key_env=_string(ai_table, "api_key_env", "", "ai"),
        base_url=_string(ai_table, "base_url", "", "ai"),
    )
    if ai.max_calls < 0:
        raise ConfigError("ai.max_calls must not be negative")
    if ai.enabled:
        raise ConfigError(
            "AI-assisted processing is not implemented in DATUM 0.1.0; set ai.enabled = false. "
            "The provider interface exists for future adapters, but the core build is deterministic."
        )

    manifest_table = _expect_table(root.get("manifest", {}), "manifest")
    _check_unknown(manifest_table, {"include_input_hashes"}, "manifest")
    manifest = ManifestOptions(
        include_input_hashes=_boolean(manifest_table, "include_input_hashes", True, "manifest"),
    )

    completion_separator = _string(root, "completion_separator", "", "completion_separator")
    if provenance.mode in ("inline", "both"):
        if provenance.inline_field in mapping.fields:
            raise ConfigError("provenance.inline_field collides with a mapped output field")
    if chunking.enabled and chunking.unit == "chars" and chunking.size > length.max_chars:
        raise ConfigError("chunking.size must not exceed length.max_chars for character chunking")

    return Config(
        path=config_path,
        config_version=config_version,
        source=source,
        limits=limits,
        normalize=normalize,
        chunking=chunking,
        length=length,
        schema=schema,
        mapping=mapping,
        dedup=dedup,
        split=split,
        provenance=provenance,
        output=output,
        quality=quality,
        errors=errors,
        cache=cache,
        runtime=runtime,
        pdf=pdf,
        ai=ai,
        manifest=manifest,
        completion_separator=completion_separator,
    )
