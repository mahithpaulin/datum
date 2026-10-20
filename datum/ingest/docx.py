"""DOCX text extraction using the Python standard library."""

from __future__ import annotations

import io
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from ..config import Config
from ..errors import ExtractionError
from .base import (
    ExtractedRecord,
    ExtractionSummary,
    IssueCallback,
    RecordCallback,
    check_file_size,
    read_all_with_digest,
)

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{WORD_NS}}}"


def _paragraph_text(paragraph: ET.Element) -> str:
    parts: list[str] = []
    for element in paragraph.iter():
        tag = element.tag
        if tag == f"{W}t":
            if element.text:
                parts.append(element.text)
        elif tag == f"{W}tab":
            parts.append("\t")
        elif tag in (f"{W}br", f"{W}cr"):
            parts.append("\n")
        elif tag == f"{W}noBreakHyphen":
            parts.append("-")
    return "".join(parts)


def _block_lines(element: ET.Element, lines: list[str], stats: dict[str, int]) -> None:
    for child in list(element):
        tag = child.tag
        if tag == f"{W}p":
            lines.append(_paragraph_text(child))
            stats["paragraphs"] = stats.get("paragraphs", 0) + 1
        elif tag == f"{W}tbl":
            stats["tables"] = stats.get("tables", 0) + 1
            for row in child.findall(f"{W}tr"):
                cells: list[str] = []
                for cell in row.findall(f"{W}tc"):
                    cell_lines: list[str] = []
                    _block_lines(cell, cell_lines, stats)
                    cells.append(" ".join(cell_lines).strip())
                lines.append("\t".join(cells))
        elif tag == f"{W}sdt":
            content = child.find(f"{W}sdtContent")
            if content is not None:
                _block_lines(content, lines, stats)
        else:
            if child.find(f".//{W}p") is not None or child.find(f".//{W}tbl") is not None:
                _block_lines(child, lines, stats)


class DocxAdapter:
    name = "docx"
    version = "1"

    def extract(
        self,
        path: Path,
        detection_format: str,
        config: Config,
        on_record: RecordCallback,
        on_issue: IssueCallback,
    ) -> ExtractionSummary:
        check_file_size(path, config.limits.max_file_bytes)
        data, digest, size = read_all_with_digest(path, config.limits.max_file_bytes)
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except (zipfile.BadZipFile, OSError) as error:
            raise ExtractionError(f"not a valid DOCX/ZIP container: {error}") from error
        with archive:
            names = set(archive.namelist())
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise ExtractionError("ZIP container is missing DOCX document parts")
            if any(info.flag_bits & 0x1 for info in archive.infolist()):
                raise ExtractionError("encrypted DOCX containers are not supported")
            uncompressed = 0
            compressed = 0
            for info in archive.infolist():
                uncompressed += info.file_size
                compressed += info.compress_size
            if uncompressed > config.limits.max_archive_uncompressed_bytes:
                raise ExtractionError(
                    "DOCX uncompressed size exceeds limits.max_archive_uncompressed_bytes"
                )
            if compressed > 0 and uncompressed > 10 * 1024 * 1024 and uncompressed / compressed > 200:
                raise ExtractionError("DOCX compression ratio is suspiciously high; refusing to parse")
            try:
                document_info = archive.getinfo("word/document.xml")
                if document_info.file_size > config.limits.max_archive_uncompressed_bytes:
                    raise ExtractionError("word/document.xml exceeds the archive size limit")
                xml_bytes = archive.read("word/document.xml")
            except (KeyError, RuntimeError, OSError, zipfile.BadZipFile) as error:
                raise ExtractionError(f"cannot read DOCX document XML: {error}") from error
        lowered = xml_bytes[:4096].lower()
        if b"<!doctype" in lowered or b"<!entity" in lowered:
            raise ExtractionError("DOCX document XML contains a DTD or entity declaration; refusing to parse")
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as error:
            raise ExtractionError(f"invalid DOCX document XML: {error}") from error
        body = root.find(f"{W}body")
        if body is None:
            raise ExtractionError("DOCX document has no body")
        lines: list[str] = []
        stats: dict[str, int] = {"paragraphs": 0, "tables": 0}
        _block_lines(body, lines, stats)
        text = "\n".join(lines)
        warnings: list[str] = []
        if any(name.startswith("word/header") or name.startswith("word/footer") for name in names):
            warnings.append("DOCX headers and footers are not extracted in DATUM 0.1.0")
        if not text.strip():
            warnings.append("DOCX contains no extractable body text")
        on_record(
            ExtractedRecord(
                ordinal=1,
                kind="text",
                locator="word/document.xml",
                text=text,
                metadata={"paragraphs": stats.get("paragraphs", 0), "tables": stats.get("tables", 0)},
            )
        )
        return ExtractionSummary(
            file_format="docx",
            adapter=self.name,
            adapter_version=self.version,
            file_sha256=digest,
            size_bytes=size,
            encoding=None,
            records_extracted=1,
            warnings=tuple(warnings),
            metadata={
                "engine": "stdlib-docx",
                "paragraphs": stats.get("paragraphs", 0),
                "tables": stats.get("tables", 0),
            },
        )
