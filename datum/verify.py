"""Independent verification of exported datasets and build manifests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from . import MANIFEST_SCHEMA_VERSION, QUALITY_SCHEMA_VERSION
from .errors import VerificationError
from .manifest import output_identity
from .schema import SchemaSpec, schema_spec_from_dict, validate_record
from .util import sha256_file, strict_json_loads


@dataclass
class ValidationStats:
    path: str
    records: int = 0
    bytes: int = 0
    invalid: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.invalid == 0


@dataclass
class VerificationReport:
    ok: bool
    checks: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manifest: dict[str, Any] | None = None
    manifest_path: Path | None = None
    output_identity: str | None = None

    def add_check(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append({"name": name, "passed": passed, "detail": detail})
        if not passed:
            self.errors.append(f"{name}: {detail}" if detail else name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "manifest_path": None if self.manifest_path is None else str(self.manifest_path),
            "build_id": None
            if self.manifest is None
            else self.manifest.get("build", {}).get("build_id"),
            "output_identity": self.output_identity,
            "checks": self.checks,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def find_manifest(build_path: str | Path) -> Path:
    path = Path(build_path).expanduser().resolve()
    if path.is_file():
        return path
    candidates = [path / "manifest.json", path / "current" / "manifest.json"]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise VerificationError(f"manifest.json not found under {path}")


def safe_relative(base: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise VerificationError(f"manifest contains an unsafe relative path: {relative}")
    resolved = (base / candidate).resolve()
    if not resolved.is_relative_to(base.resolve()):
        raise VerificationError(f"manifest output path escapes the build directory: {relative}")
    return resolved


def validate_jsonl_file(
    path: Path,
    schema: SchemaSpec,
    *,
    inline_field: str | None = None,
    require_inline: bool = False,
    max_errors: int = 50,
) -> ValidationStats:
    stats = ValidationStats(path=str(path))
    try:
        size = path.stat().st_size
    except OSError as error:
        stats.invalid += 1
        stats.errors.append(f"cannot stat file: {error}")
        return stats
    stats.bytes = size
    try:
        handle = path.open("r", encoding="utf-8")
    except OSError as error:
        stats.invalid += 1
        stats.errors.append(f"cannot open file: {error}")
        return stats
    with handle:
        try:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    stats.invalid += 1
                    if len(stats.errors) < max_errors:
                        stats.errors.append(f"line {line_number}: blank line is not valid JSONL")
                    continue
                try:
                    value = strict_json_loads(line)
                except (json.JSONDecodeError, ValueError) as error:
                    stats.invalid += 1
                    if len(stats.errors) < max_errors:
                        stats.errors.append(f"line {line_number}: invalid JSON ({error})")
                    continue
                if inline_field:
                    if not isinstance(value, dict):
                        stats.invalid += 1
                        if len(stats.errors) < max_errors:
                            stats.errors.append(f"line {line_number}: record is not a JSON object")
                        continue
                    if require_inline and inline_field not in value:
                        stats.invalid += 1
                        if len(stats.errors) < max_errors:
                            stats.errors.append(
                                f"line {line_number}: inline provenance field '{inline_field}' is missing"
                            )
                    value = {key: item for key, item in value.items() if key != inline_field}
                problems = validate_record(value, schema)
                if problems:
                    stats.invalid += 1
                    if len(stats.errors) < max_errors:
                        stats.errors.append(f"line {line_number}: " + "; ".join(problems[:5]))
                stats.records += 1
        except UnicodeError as error:
            stats.invalid += 1
            stats.errors.append(f"file is not valid UTF-8: {error}")
    return stats


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except FileNotFoundError as error:
        raise VerificationError(f"manifest not found: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError(f"cannot read manifest {path}: {error}") from error
    if not isinstance(manifest, dict):
        raise VerificationError("manifest is not a JSON object")
    if manifest.get("manifest_schema_version") != MANIFEST_SCHEMA_VERSION:
        raise VerificationError(
            f"unsupported manifest schema version: {manifest.get('manifest_schema_version')}"
        )
    return manifest


def verify_build(build_path: str | Path, *, check_sources: bool = False) -> VerificationReport:
    report = VerificationReport(ok=False)
    try:
        manifest_path = find_manifest(build_path)
        manifest = _read_manifest(manifest_path)
    except VerificationError as error:
        report.add_check("manifest_readable", False, str(error))
        return report
    report.manifest = manifest
    report.manifest_path = manifest_path
    base = manifest_path.parent
    report.add_check("manifest_readable", True, str(manifest_path))
    build = manifest.get("build", {})
    status = build.get("status")
    report.add_check(
        "build_status",
        status in ("complete", "quality_failed"),
        f"status={status}",
    )
    try:
        schema = schema_spec_from_dict(manifest.get("schema", {}))
        report.add_check("schema_readable", True, f"{schema.name} v{schema.version}")
    except Exception as error:
        report.add_check("schema_readable", False, str(error))
        return report
    config = manifest.get("config", {})
    provenance_mode = config.get("provenance", {}).get("mode", "sidecar")
    inline_field = (
        config.get("provenance", {}).get("inline_field", "_datum")
        if provenance_mode in ("inline", "both")
        else None
    )
    require_inline = provenance_mode in ("inline", "both")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        report.add_check("outputs_declared", False, "manifest has no outputs")
        return report
    report.add_check("outputs_declared", True, f"{len(outputs)} file(s)")
    total_records = 0
    output_identity_entries: list[dict[str, Any]] = []
    output_bytes = 0
    split_counts: dict[str, int] = {}
    output_record_limits: dict[str, int] = {}
    for item in outputs:
        try:
            relative = item["path"]
            expected_sha = item["sha256"]
            expected_bytes = int(item["bytes"])
            expected_records = int(item["records"])
            path = safe_relative(base, relative)
        except (KeyError, TypeError, ValueError, VerificationError) as error:
            report.add_check("output_entry", False, f"invalid output entry: {error}")
            continue
        if not path.is_file():
            report.add_check("output_exists", False, f"missing output: {relative}")
            continue
        digest, size = sha256_file(path)
        report.add_check("output_sha256", digest == expected_sha, f"{relative} sha256")
        report.add_check(
            "output_bytes",
            size == expected_bytes,
            f"{relative}: actual={size} expected={expected_bytes}",
        )
        stats = validate_jsonl_file(
            path,
            schema,
            inline_field=inline_field,
            require_inline=require_inline,
        )
        report.add_check(
            "output_jsonl_and_schema",
            stats.invalid == 0,
            f"{relative}: {stats.records} records, {stats.invalid} invalid"
            + ("" if not stats.errors else f"; first error: {stats.errors[0]}"),
        )
        report.add_check(
            "output_record_count",
            stats.records == expected_records,
            f"{relative}: actual={stats.records} manifest={expected_records}",
        )
        total_records += stats.records
        output_bytes += size
        output_record_limits[relative] = stats.records
        split_name = Path(relative).stem
        split_counts[split_name] = split_counts.get(split_name, 0) + stats.records
        output_identity_entries.append(
            {"path": relative, "sha256": digest, "bytes": size, "records": stats.records}
        )
    sidecar_info = manifest.get("sidecar")
    sidecar_entry = None
    if sidecar_info:
        try:
            sidecar_path = safe_relative(base, sidecar_info["path"])
            expected_sha = sidecar_info["sha256"]
            expected_bytes = int(sidecar_info["bytes"])
            expected_records = int(sidecar_info["records"])
        except (KeyError, TypeError, ValueError, VerificationError) as error:
            report.add_check("sidecar_entry", False, f"invalid sidecar entry: {error}")
        else:
            if sidecar_path.is_file():
                digest, size = sha256_file(sidecar_path)
                report.add_check("sidecar_sha256", digest == expected_sha, "provenance sha256")
                report.add_check(
                    "sidecar_bytes",
                    size == expected_bytes,
                    f"actual={size} expected={expected_bytes}",
                )
                seen_ids: set[str] = set()
                lines = 0
                parse_ok = True
                try:
                    with sidecar_path.open("r", encoding="utf-8") as handle:
                        for line in handle:
                            if not line.strip():
                                continue
                            value = strict_json_loads(line)
                            if not isinstance(value, dict):
                                parse_ok = False
                                break
                            record_id = value.get("record_id")
                            if not isinstance(record_id, str) or not record_id:
                                parse_ok = False
                                break
                            if record_id in seen_ids:
                                parse_ok = False
                                break
                            output_file = value.get("output_file")
                            output_line = value.get("output_line")
                            limit = output_record_limits.get(output_file)
                            if (
                                limit is None
                                or isinstance(output_line, bool)
                                or not isinstance(output_line, int)
                                or output_line < 1
                                or output_line > limit
                            ):
                                parse_ok = False
                                break
                            seen_ids.add(record_id)
                            lines += 1
                except (OSError, ValueError, json.JSONDecodeError):
                    parse_ok = False
                report.add_check("sidecar_parse", parse_ok, "provenance records parse and IDs are unique")
                report.add_check(
                    "sidecar_record_count",
                    lines == expected_records == total_records,
                    f"actual={lines} manifest={expected_records} exported={total_records}",
                )
                sidecar_entry = {
                    "path": sidecar_info["path"],
                    "sha256": digest,
                    "bytes": size,
                    "records": lines,
                }
            else:
                report.add_check("sidecar_exists", False, "provenance.jsonl is missing")
    elif provenance_mode in ("sidecar", "both"):
        report.add_check("sidecar_exists", False, "manifest declares no provenance sidecar")
    else:
        report.add_check("sidecar_not_required", True, f"provenance mode={provenance_mode}")

    rejected_manifest = manifest.get("rejected")
    if rejected_manifest:
        try:
            rejected_manifest_path = safe_relative(base, rejected_manifest["path"])
            expected_rejected_sha = rejected_manifest["sha256"]
            expected_rejected_bytes = int(rejected_manifest["bytes"])
        except (KeyError, TypeError, ValueError, VerificationError) as error:
            report.add_check("rejected_entry", False, f"invalid rejected entry: {error}")
            rejected_manifest_path = None
        if rejected_manifest_path is not None:
            if rejected_manifest_path.is_file():
                rejected_digest, rejected_size = sha256_file(rejected_manifest_path)
                report.add_check(
                    "rejected_sha256",
                    rejected_digest == expected_rejected_sha,
                    "rejected.jsonl sha256",
                )
                report.add_check(
                    "rejected_bytes",
                    rejected_size == expected_rejected_bytes,
                    f"actual={rejected_size} expected={expected_rejected_bytes}",
                )
            else:
                report.add_check("rejected_exists", False, "rejected.jsonl is missing")

    quality_path = base / "quality.json"
    quality: Mapping[str, Any] = {}
    if quality_path.is_file():
        try:
            with quality_path.open("r", encoding="utf-8") as handle:
                quality = json.load(handle)
            report.add_check("quality_readable", True, "quality.json")
        except (OSError, json.JSONDecodeError) as error:
            report.add_check("quality_readable", False, str(error))
    else:
        report.add_check("quality_readable", False, "quality.json is missing")
    if quality:
        report.add_check(
            "quality_schema_version",
            quality.get("schema_version") == QUALITY_SCHEMA_VERSION,
            f"quality schema version={quality.get('schema_version')}",
        )
        quality_exported = quality.get("records", {}).get("exported")
        report.add_check(
            "quality_exported_count",
            quality_exported == total_records,
            f"quality={quality_exported} actual={total_records}",
        )
        report.add_check(
            "quality_invariants",
            all(row.get("passed") for row in quality.get("invariants", [])),
            "all recorded quality invariants passed",
        )
        report.add_check(
            "quality_status",
            quality.get("status") in ("passed", "quality_failed"),
            f"quality status={quality.get('status')}",
        )
        quality_status = quality.get("status")
        build_status = build.get("status")
        status_matches = (
            (build_status == "complete" and quality_status == "passed")
            or (build_status == "quality_failed" and quality_status == "quality_failed")
        )
        report.add_check(
            "manifest_quality_status",
            status_matches,
            f"manifest={build_status} quality={quality_status}",
        )
        reported_splits = quality.get("splits", {})
        if config.get("split", {}).get("enabled"):
            report.add_check(
                "split_counts",
                reported_splits == split_counts,
                f"quality={reported_splits} actual={split_counts}",
            )
        else:
            report.add_check(
                "split_counts",
                total_records == quality.get("records", {}).get("exported", -1),
                "single dataset split",
            )
        output_bytes_quality = quality.get("performance", {}).get("output_bytes")
        report.add_check(
            "quality_output_bytes",
            output_bytes_quality == output_bytes,
            f"quality={output_bytes_quality} actual={output_bytes}",
        )
        rejected_info = quality.get("rejected", {})
        rejected_path = base / "rejected.jsonl"
        rejected_lines = 0
        rejected_ok = True
        if rejected_path.is_file():
            try:
                with rejected_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        if not line.strip():
                            continue
                        strict_json_loads(line)
                        rejected_lines += 1
            except (OSError, ValueError, json.JSONDecodeError):
                rejected_ok = False
        else:
            rejected_ok = False
        report.add_check("rejected_log_parses", rejected_ok, "rejected.jsonl")
        report.add_check(
            "rejected_log_count",
            rejected_lines == rejected_info.get("log_entries"),
            f"actual={rejected_lines} quality={rejected_info.get('log_entries')}",
        )
    try:
        identity = output_identity(output_identity_entries, sidecar_entry)
        report.output_identity = identity
        declared_identity = manifest.get("output_identity") or build.get("output_identity")
        report.add_check(
            "output_identity",
            identity == declared_identity,
            f"computed={identity} declared={declared_identity}",
        )
    except Exception as error:
        report.add_check("output_identity", False, str(error))

    if check_sources:
        inputs = manifest.get("inputs", [])
        source_root_raw = config.get("source", {}).get("path")
        if not isinstance(source_root_raw, str):
            report.add_check("source_root", False, "config.source.path is missing from manifest")
        else:
            source_root = Path(source_root_raw)
            if not source_root.is_dir():
                report.add_check("source_root", False, f"source root is unavailable: {source_root}")
            else:
                failures = 0
                checked = 0
                for item in inputs:
                    expected = item.get("sha256")
                    if not expected or item.get("status") != "processed":
                        continue
                    try:
                        source_path = safe_relative(source_root, item["path"])
                    except (KeyError, VerificationError):
                        failures += 1
                        continue
                    checked += 1
                    if not source_path.is_file():
                        failures += 1
                        continue
                    digest, _size = sha256_file(source_path)
                    if digest != expected:
                        failures += 1
                report.add_check(
                    "source_hashes",
                    failures == 0,
                    f"checked={checked} failures={failures}",
                )

    report.ok = not report.errors
    return report
