"""CSV/TSV, JSON, and JSONL ingestion."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from ..config import Config
from ..errors import ExtractionError
from ..util import strict_json_loads
from .base import (
    ExtractedRecord,
    ExtractionSummary,
    IssueCallback,
    RecordCallback,
    check_file_size,
    decode_bytes,
    hashing_text_reader,
    head_bytes,
    read_all_with_digest,
    resolve_encoding,
)

DELIMITERS = {"comma": ",", "tab": "\t", "semicolon": ";", "pipe": "|"}


def _delimiter_for(path: Path, detection_format: str, config: Config) -> str:
    if detection_format == "tsv":
        return "\t"
    configured = config.source.csv_delimiter
    if configured != "auto":
        return DELIMITERS.get(configured, configured)
    try:
        with path.open("rb") as handle:
            sample = handle.read(8192).decode("utf-8", "replace")
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except (OSError, csv.Error):
        return ","


def _count_replacements_row(row: dict[str, Any]) -> int:
    total = 0
    for key, value in row.items():
        if isinstance(key, str):
            total += key.count("\ufffd")
        if isinstance(value, str):
            total += value.count("\ufffd")
    return total


class DelimitedAdapter:
    name = "delimited"
    version = "1"

    def extract(
        self,
        path: Path,
        detection_format: str,
        config: Config,
        on_record: RecordCallback,
        on_issue: IssueCallback,
    ) -> ExtractionSummary:
        from .base import RecordIssue

        declared_size = check_file_size(path, config.limits.max_file_bytes)
        delimiter = _delimiter_for(path, detection_format, config)
        encoding = resolve_encoding(config.source.encoding, head_bytes(path))
        malformed = 0
        replacements = 0
        extracted = 0
        warnings: list[str] = []
        fieldnames: list[str] = []
        reader_obj: Any = None
        reader_size = 0
        digest = ""
        with hashing_text_reader(path, encoding, config.source.encoding_errors) as (text, reader):
            try:
                if config.source.csv_has_header:
                    reader_obj = csv.DictReader(text, delimiter=delimiter, strict=True)
                    raw_fields = reader_obj.fieldnames
                    if raw_fields is None:
                        raise ExtractionError("CSV file has no header row")
                    fieldnames = [name if name is not None else "" for name in raw_fields]
                    if any(not name.strip() for name in fieldnames):
                        raise ExtractionError("CSV header contains an empty column name")
                    if len(set(fieldnames)) != len(fieldnames):
                        raise ExtractionError("CSV header contains duplicate column names")
                    replacements += sum(name.count("\ufffd") for name in fieldnames)
                    for row in reader_obj:
                        line_number = reader_obj.line_num
                        if row is None:
                            continue
                        if all((value is None or value == "") for value in row.values()):
                            continue
                        if None in row or any(value is None for value in row.values()):
                            malformed += 1
                            on_issue(
                                RecordIssue(
                                    code="malformed_csv_row",
                                    message="row has a different number of columns than the header",
                                    line=line_number,
                                )
                            )
                            continue
                        extracted += 1
                        if extracted > config.limits.max_records_per_file:
                            raise ExtractionError(
                                f"file exceeds limits.max_records_per_file ({config.limits.max_records_per_file})"
                            )
                        replacements += _count_replacements_row(row)
                        on_record(
                            ExtractedRecord(
                                ordinal=extracted,
                                kind="data",
                                locator=f"line {line_number}",
                                data=dict(row),
                                metadata={"line": line_number},
                            )
                        )
                else:
                    reader_obj = csv.reader(text, delimiter=delimiter, strict=True)
                    width = 0
                    for row in reader_obj:
                        if not row:
                            continue
                        line_number = reader_obj.line_num
                        if not fieldnames:
                            width = len(row)
                            if width == 0:
                                raise ExtractionError("CSV file has no columns")
                            fieldnames = [f"column_{index + 1}" for index in range(width)]
                        if len(row) != width:
                            malformed += 1
                            on_issue(
                                RecordIssue(
                                    code="malformed_csv_row",
                                    message=f"row has {len(row)} columns; expected {width}",
                                    line=line_number,
                                )
                            )
                            continue
                        extracted += 1
                        if extracted > config.limits.max_records_per_file:
                            raise ExtractionError(
                                f"file exceeds limits.max_records_per_file ({config.limits.max_records_per_file})"
                            )
                        data = dict(zip(fieldnames, row))
                        replacements += _count_replacements_row(data)
                        on_record(
                            ExtractedRecord(
                                ordinal=extracted,
                                kind="data",
                                locator=f"line {line_number}",
                                data=data,
                                metadata={"line": line_number},
                            )
                        )
                    if not fieldnames:
                        raise ExtractionError("CSV file is empty")
            except csv.Error as error:
                line_number = getattr(reader_obj, "line_num", None)
                raise ExtractionError(f"CSV parsing failed near line {line_number}: {error}") from error
            except UnicodeError as error:
                raise ExtractionError(f"CSV decoding failed as {encoding}: {error}") from error
            reader_size = reader.bytes_read
            digest = reader.hexdigest
        size = reader_size
        if size != declared_size:
            warnings.append(f"file size changed during ingestion ({declared_size} -> {size} bytes)")
        if replacements:
            warnings.append(f"decoded {replacements} replacement character(s) using errors=replace")
        return ExtractionSummary(
            file_format=detection_format,
            adapter=self.name,
            adapter_version=self.version,
            file_sha256=digest,
            size_bytes=size,
            encoding=encoding,
            records_extracted=extracted,
            malformed_records=malformed,
            encoding_replacements=replacements,
            warnings=tuple(warnings),
            metadata={
                "delimiter": delimiter,
                "has_header": config.source.csv_has_header,
                "fieldnames": fieldnames,
            },
        )


def _lookup_path(value: Any, path: str) -> Any:
    if not path:
        return value
    current = value
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                raise ExtractionError(f"json_records_path segment not found: {part}")
            current = current[part]
        elif isinstance(current, list):
            try:
                index = int(part)
            except ValueError as error:
                raise ExtractionError(f"json_records_path expects an array index, got: {part}") from error
            if index < 0 or index >= len(current):
                raise ExtractionError(f"json_records_path array index out of range: {index}")
            current = current[index]
        else:
            raise ExtractionError(f"json_records_path cannot descend through a scalar at: {part}")
    return current


class JsonAdapter:
    name = "json"
    version = "1"

    def extract(
        self,
        path: Path,
        detection_format: str,
        config: Config,
        on_record: RecordCallback,
        on_issue: IssueCallback,
    ) -> ExtractionSummary:
        declared_size = check_file_size(path, config.limits.max_file_bytes)
        data, digest, size = read_all_with_digest(path, config.limits.max_file_bytes)
        encoding = resolve_encoding(config.source.encoding, head_bytes(path))
        text, replacements = decode_bytes(data, encoding, config.source.encoding_errors)
        if size != declared_size:
            raise ExtractionError("file size changed while it was being read")
        try:
            document = strict_json_loads(text)
        except (json.JSONDecodeError, ValueError) as error:
            raise ExtractionError(f"invalid JSON: {error}") from error
        root: Any
        if config.source.json_records_path:
            root = _lookup_path(document, config.source.json_records_path)
            if not isinstance(root, list):
                raise ExtractionError(
                    f"json_records_path '{config.source.json_records_path}' must resolve to an array"
                )
        elif isinstance(document, list):
            root = document
        else:
            root = [document]
        warnings: list[str] = []
        if replacements:
            warnings.append(f"decoded {replacements} replacement character(s) using errors=replace")
        extracted = 0
        for index, item in enumerate(root):
            extracted += 1
            if extracted > config.limits.max_records_per_file:
                raise ExtractionError(
                    f"file exceeds limits.max_records_per_file ({config.limits.max_records_per_file})"
                )
            on_record(
                ExtractedRecord(
                    ordinal=extracted,
                    kind="data",
                    locator=f"$[{index}]" if isinstance(root, list) and isinstance(document, list) else f"$.{index}",
                    data=item,
                    metadata={"index": index},
                )
            )
        return ExtractionSummary(
            file_format=detection_format,
            adapter=self.name,
            adapter_version=self.version,
            file_sha256=digest,
            size_bytes=size,
            encoding=encoding,
            records_extracted=extracted,
            encoding_replacements=replacements,
            warnings=tuple(warnings),
            metadata={
                "top_level": type(document).__name__,
                "records_path": config.source.json_records_path,
            },
        )


class JsonlAdapter:
    name = "jsonl"
    version = "1"

    def extract(
        self,
        path: Path,
        detection_format: str,
        config: Config,
        on_record: RecordCallback,
        on_issue: IssueCallback,
    ) -> ExtractionSummary:
        declared_size = check_file_size(path, config.limits.max_file_bytes)
        encoding = resolve_encoding(config.source.encoding, head_bytes(path))
        extracted = 0
        malformed = 0
        replacements = 0
        warnings: list[str] = []
        from .base import RecordIssue

        with hashing_text_reader(path, encoding, config.source.encoding_errors) as (text, reader):
            try:
                for line_number, line in enumerate(text, 1):
                    if line_number == 1 and line.startswith("\ufeff"):
                        line = line[1:]
                    replacements += line.count("\ufffd")
                    stripped = line.strip()
                    if not stripped:
                        if not config.source.jsonl_skip_blank:
                            malformed += 1
                            on_issue(
                                RecordIssue(
                                    code="blank_jsonl_line",
                                    message="blank lines are disabled by source.jsonl_skip_blank = false",
                                    line=line_number,
                                )
                            )
                        continue
                    try:
                        value = strict_json_loads(stripped)
                    except (json.JSONDecodeError, ValueError) as error:
                        malformed += 1
                        on_issue(
                            RecordIssue(
                                code="malformed_json",
                                message=f"invalid JSON on line {line_number}: {error.msg if isinstance(error, json.JSONDecodeError) else error}",
                                line=line_number,
                            )
                        )
                        continue
                    extracted += 1
                    if extracted > config.limits.max_records_per_file:
                        raise ExtractionError(
                            f"file exceeds limits.max_records_per_file ({config.limits.max_records_per_file})"
                        )
                    on_record(
                        ExtractedRecord(
                            ordinal=extracted,
                            kind="data",
                            locator=f"line {line_number}",
                            data=value,
                            metadata={"line": line_number},
                        )
                    )
            except UnicodeError as error:
                raise ExtractionError(f"JSONL decoding failed as {encoding}: {error}") from error
            size = reader.bytes_read
        if size != declared_size:
            warnings.append(f"file size changed during ingestion ({declared_size} -> {size} bytes)")
        if replacements:
            warnings.append(f"decoded {replacements} replacement character(s) using errors=replace")
        return ExtractionSummary(
            file_format=detection_format,
            adapter=self.name,
            adapter_version=self.version,
            file_sha256=reader.hexdigest,
            size_bytes=size,
            encoding=encoding,
            records_extracted=extracted,
            malformed_records=malformed,
            encoding_replacements=replacements,
            warnings=tuple(warnings),
        )
