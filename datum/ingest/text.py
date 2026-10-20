"""Plain text, Markdown, and source-code ingestion."""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..errors import ExtractionError
from .base import (
    ExtractedRecord,
    ExtractionSummary,
    IssueCallback,
    RecordCallback,
    check_file_size,
    decode_bytes,
    head_bytes,
    read_all_with_digest,
    resolve_encoding,
)


class TextAdapter:
    name = "text"
    version = "1"

    def extract(
        self,
        path: Path,
        detection_format: str,
        config: Config,
        on_record: RecordCallback,
        on_issue: IssueCallback,
    ) -> ExtractionSummary:
        size = check_file_size(path, config.limits.max_file_bytes)
        data, digest, actual_size = read_all_with_digest(path, config.limits.max_file_bytes)
        encoding = resolve_encoding(config.source.encoding, head_bytes(path))
        text, replacements = decode_bytes(data, encoding, config.source.encoding_errors)
        if actual_size != size:
            raise ExtractionError("file size changed while it was being read")
        warnings: list[str] = []
        if replacements:
            warnings.append(
                f"decoded {replacements} invalid or replacement byte sequence(s) using errors=replace"
            )
        on_record(
            ExtractedRecord(
                ordinal=1,
                kind="text",
                locator="document",
                text=text,
                metadata={"line_count": text.count("\n") + (1 if text else 0)},
            )
        )
        return ExtractionSummary(
            file_format=detection_format,
            adapter=self.name,
            adapter_version=self.version,
            file_sha256=digest,
            size_bytes=actual_size,
            encoding=encoding,
            records_extracted=1,
            encoding_replacements=replacements,
            warnings=tuple(warnings),
            metadata={"line_count": text.count("\n") + (1 if text else 0)},
        )
