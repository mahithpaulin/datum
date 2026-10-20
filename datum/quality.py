"""Quality accounting, reconciliation, and reporting."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from . import QUALITY_SCHEMA_VERSION
from .config import QualityOptions
from .transform.normalize import NormalizeStats
from .util import human_bytes, human_duration, length_distribution, safe_ratio


@dataclass
class FileCounters:
    discovered: int = 0
    processed: int = 0
    processed_with_errors: int = 0
    failed: int = 0
    skipped: int = 0
    empty_files: int = 0
    input_bytes_discovered: int = 0
    input_bytes_processed: int = 0
    excluded_by_config: int = 0
    hidden_skipped: int = 0
    symlinks_skipped: int = 0
    non_regular_skipped: int = 0
    duplicate_targets_skipped: int = 0
    walk_issues: int = 0


@dataclass
class RecordCounters:
    source_records_extracted: int = 0
    source_records_mapped: int = 0
    mapping_rejected: int = 0
    chunks_created: int = 0
    source_records_split: int = 0
    empty: int = 0
    too_short: int = 0
    too_long: int = 0
    schema_rejected: int = 0
    duplicates: int = 0
    exported: int = 0
    truncated: int = 0
    malformed_input: int = 0

    @property
    def rejected_total(self) -> int:
        return (
            self.mapping_rejected
            + self.empty
            + self.too_short
            + self.too_long
            + self.schema_rejected
            + self.duplicates
        )

    @property
    def retained_pre_dedup(self) -> int:
        return self.chunks_created - self.empty - self.too_short - self.too_long - self.schema_rejected


@dataclass
class ErrorCollector:
    max_examples: int = 50
    counts: dict[str, int] = field(default_factory=dict)
    examples: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0

    def add(
        self,
        code: str,
        message: str,
        path: str | None = None,
        line: int | None = None,
        severity: str = "error",
    ) -> None:
        self.total += 1
        self.counts[code] = self.counts.get(code, 0) + 1
        if len(self.examples) < self.max_examples:
            example: dict[str, Any] = {"code": code, "severity": severity, "message": message}
            if path is not None:
                example["path"] = path
            if line is not None:
                example["line"] = line
            self.examples.append(example)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "by_code": dict(sorted(self.counts.items())),
            "examples": list(self.examples),
            "examples_truncated": self.total > len(self.examples),
        }


@dataclass
class TransformationCounters:
    normalization: NormalizeStats = field(default_factory=NormalizeStats)
    encoding_replacements: int = 0
    truncated_records: int = 0
    warnings: int = 0

    def add_normalization(self, stats: NormalizeStats) -> None:
        self.normalization.add(stats)


@dataclass
class QualitySummary:
    status: str
    report: dict[str, Any]
    text: str
    invariant_failures: list[str]
    threshold_failures: list[str]


def _rate(numerator: int, denominator: int) -> float:
    ratio = safe_ratio(numerator, denominator)
    return 0.0 if ratio is None else ratio


def build_quality_summary(
    *,
    build_id: str,
    files: FileCounters,
    records: RecordCounters,
    schema_name: str,
    schema_version: int,
    lengths: list[int],
    split_counts: Mapping[str, int],
    transformations: TransformationCounters,
    performance: Mapping[str, Any],
    cache: Mapping[str, Any],
    errors: ErrorCollector,
    rejected_by_rule: Mapping[str, int],
    quality_options: QualityOptions,
    rejected_log_entries: int,
) -> QualitySummary:
    levels = dict(records.__dict__)
    levels["rejected_total"] = records.rejected_total
    levels["retained_pre_dedup"] = records.retained_pre_dedup
    levels["excluded_total"] = records.rejected_total + records.malformed_input

    schema_checked = (
        records.chunks_created - records.empty - records.too_short - records.too_long
    )
    schema_compliance = (
        None
        if schema_checked <= 0
        else (schema_checked - records.schema_rejected) / schema_checked
    )
    rates = {
        "file_error_rate": _rate(files.failed, files.discovered),
        "file_skip_rate": _rate(files.skipped, files.discovered),
        "record_duplicate_rate": _rate(records.duplicates, records.retained_pre_dedup),
        "schema_rejection_rate": _rate(records.schema_rejected, schema_checked),
        "schema_compliance": schema_compliance,
        "mapping_rejection_rate": _rate(records.mapping_rejected, records.source_records_extracted),
    }

    invariants = [
        (
            "file_accounting",
            files.discovered == files.processed + files.failed + files.skipped,
            f"discovered={files.discovered} processed={files.processed} failed={files.failed} skipped={files.skipped}",
        ),
        (
            "processed_with_errors_subset",
            files.processed_with_errors <= files.processed,
            f"processed_with_errors={files.processed_with_errors} processed={files.processed}",
        ),
        (
            "source_record_mapping",
            records.source_records_extracted
            == records.source_records_mapped + records.mapping_rejected,
            f"extracted={records.source_records_extracted} mapped={records.source_records_mapped} "
            f"mapping_rejected={records.mapping_rejected}",
        ),
        (
            "candidate_outcomes",
            records.chunks_created
            == records.empty
            + records.too_short
            + records.too_long
            + records.schema_rejected
            + records.duplicates
            + records.exported,
            f"chunks={records.chunks_created} empty={records.empty} too_short={records.too_short} "
            f"too_long={records.too_long} schema_rejected={records.schema_rejected} "
            f"duplicates={records.duplicates} exported={records.exported}",
        ),
        (
            "retention_equation",
            records.retained_pre_dedup
            == records.chunks_created
            - records.empty
            - records.too_short
            - records.too_long
            - records.schema_rejected,
            f"retained_pre_dedup={records.retained_pre_dedup}",
        ),
        (
            "export_equation",
            records.exported == records.retained_pre_dedup - records.duplicates,
            f"exported={records.exported} retained_pre_dedup={records.retained_pre_dedup} "
            f"duplicates={records.duplicates}",
        ),
        (
            "split_accounting",
            records.exported == sum(split_counts.values()),
            f"exported={records.exported} split_total={sum(split_counts.values())}",
        ),
        (
            "rejected_log_accounting",
            rejected_log_entries == records.rejected_total + records.malformed_input,
            f"log_entries={rejected_log_entries} rejected_total={records.rejected_total} "
            f"malformed_input={records.malformed_input}",
        ),
    ]
    invariant_rows = [
        {"name": name, "passed": passed, "detail": detail}
        for name, passed, detail in invariants
    ]
    invariant_failures = [row["name"] for row in invariant_rows if not row["passed"]]

    thresholds = [
        {
            "metric": "min_exported_records",
            "actual": records.exported,
            "requirement": f">= {quality_options.min_exported_records}",
            "passed": records.exported >= quality_options.min_exported_records,
        },
        {
            "metric": "max_file_error_rate",
            "actual": rates["file_error_rate"],
            "requirement": f"<= {quality_options.max_file_error_rate}",
            "passed": rates["file_error_rate"] <= quality_options.max_file_error_rate,
        },
        {
            "metric": "max_schema_rejection_rate",
            "actual": rates["schema_rejection_rate"],
            "requirement": f"<= {quality_options.max_schema_rejection_rate}",
            "passed": rates["schema_rejection_rate"] <= quality_options.max_schema_rejection_rate,
        },
        {
            "metric": "max_duplicate_rate",
            "actual": rates["record_duplicate_rate"],
            "requirement": f"<= {quality_options.max_duplicate_rate}",
            "passed": rates["record_duplicate_rate"] <= quality_options.max_duplicate_rate,
        },
    ]
    threshold_failures = [row["metric"] for row in thresholds if not row["passed"]]
    status = "quality_failed" if threshold_failures else "passed"

    report: dict[str, Any] = {
        "schema_version": QUALITY_SCHEMA_VERSION,
        "build_id": build_id,
        "status": status,
        "files": asdict(files),
        "records": levels,
        "rates": rates,
        "schema": {
            "name": schema_name,
            "version": schema_version,
            "checked": max(0, schema_checked),
            "rejected": records.schema_rejected,
            "compliance": schema_compliance,
            "note": "schema compliance is mechanical, not semantic correctness",
        },
        "lengths": length_distribution(lengths),
        "splits": dict(sorted(split_counts.items())),
        "transformation": {
            **asdict(transformations.normalization),
            "encoding_replacements": transformations.encoding_replacements,
            "truncated_records": transformations.truncated_records,
            "warnings": transformations.warnings,
        },
        "performance": dict(performance),
        "cache": dict(cache),
        "thresholds": thresholds,
        "threshold_failures": threshold_failures,
        "invariants": invariant_rows,
        "rejected": {
            "log_entries": rejected_log_entries,
            "by_rule": dict(sorted(rejected_by_rule.items())),
        },
        "errors": errors.to_dict(),
        "definitions": {
            "schema_compliance": "(schema_checked - schema_rejected) / schema_checked; null when no candidate reached schema validation",
            "record_duplicate_rate": "duplicates / retained_pre_dedup",
            "retained_pre_dedup": "candidates passing empty, length, and schema checks before exact deduplication",
            "exported": "records written to dataset files; independent verification revalidates bytes, schema, and counts",
            "file_error_rate": "failed files / discovered files",
        },
    }

    text_lines = [
        f"DATUM quality report ({status})",
        f"Build ID: {build_id}",
        f"Files: {files.discovered} discovered, {files.processed} processed "
        f"({files.processed_with_errors} with record errors), {files.failed} failed, {files.skipped} skipped",
        f"Records: {records.source_records_extracted} source records, {records.chunks_created} candidate chunks, "
        f"{records.exported} exported",
        f"Exclusions: {records.mapping_rejected} mapping, {records.empty} empty, "
        f"{records.too_short} too short, {records.too_long} too long, "
        f"{records.schema_rejected} schema-invalid, {records.duplicates} exact duplicates",
        f"Malformed input records: {records.malformed_input}",
        f"Schema: {schema_name} v{schema_version}; compliance: "
        + ("n/a" if schema_compliance is None else f"{schema_compliance * 100:.2f}%"),
        f"Splits: " + ", ".join(f"{name}={count}" for name, count in sorted(split_counts.items())),
        f"Lengths: min={report['lengths']['min']} max={report['lengths']['max']} "
        f"p50={report['lengths']['p50']} p99={report['lengths']['p99']}",
        f"Time: {human_duration(float(performance.get('total_seconds', 0.0)))}; "
        f"input {human_bytes(files.input_bytes_processed)}; "
        f"output {human_bytes(int(performance.get('output_bytes', 0)))}",
    ]
    if threshold_failures:
        text_lines.append("Quality thresholds failed: " + ", ".join(threshold_failures))
    else:
        text_lines.append("Quality thresholds: passed")
    if invariant_failures:
        text_lines.append("Critical invariants failed: " + ", ".join(invariant_failures))
    return QualitySummary(
        status=status,
        report=report,
        text="\n".join(text_lines) + "\n",
        invariant_failures=invariant_failures,
        threshold_failures=threshold_failures,
    )
