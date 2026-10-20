"""PDF text extraction with a deterministic built-in reader and an optional pypdf engine."""

from __future__ import annotations

import importlib
import io
import re
import zlib
from pathlib import Path
from typing import Any

from ..config import Config
from ..errors import DependencyError, ExtractionError
from .base import (
    ExtractedRecord,
    ExtractionSummary,
    IssueCallback,
    RecordCallback,
    check_file_size,
    read_all_with_digest,
)

OBJECT_PATTERN = re.compile(rb"(\d+)\s+(\d+)\s+obj\b(.*?)endobj", re.DOTALL)
STREAM_PATTERN = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)
ENCRYPT_PATTERN = re.compile(rb"/Encrypt\s+\d+\s+\d+\s+R")

TEXT_SHOW_OPS = {b"Tj", b"TJ", b"'", b'"'}
TEXT_MOVE_OPS = {b"Td", b"TD", b"T*", b"Tm"}
WHITESPACE = b" \t\r\n\f\x00"


def _decode_pdf_bytes(value: bytes) -> str:
    if value.startswith(b"\xfe\xff"):
        try:
            return value[2:].decode("utf-16-be", "replace")
        except UnicodeError:
            return value.decode("latin-1", "replace")
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value.decode("latin-1", "replace")


def _parse_literal_string(data: bytes, start: int) -> tuple[bytes, int]:
    result = bytearray()
    depth = 1
    index = start
    length = len(data)
    while index < length:
        byte = data[index]
        if byte == 0x5C:
            index += 1
            if index >= length:
                break
            escaped = data[index]
            mapping = {
                ord("n"): 0x0A,
                ord("r"): 0x0D,
                ord("t"): 0x09,
                ord("b"): 0x08,
                ord("f"): 0x0C,
                ord("("): 0x28,
                ord(")"): 0x29,
                ord("\\"): 0x5C,
            }
            if escaped in mapping:
                result.append(mapping[escaped])
                index += 1
                continue
            if escaped in (0x0A, 0x0D):
                if escaped == 0x0D and index + 1 < length and data[index + 1] == 0x0A:
                    index += 2
                else:
                    index += 1
                continue
            if 0x30 <= escaped <= 0x37:
                digits = bytearray()
                for _ in range(3):
                    if index < length and 0x30 <= data[index] <= 0x37:
                        digits.append(data[index])
                        index += 1
                    else:
                        break
                result.append(int(digits, 8) & 0xFF)
                continue
            result.append(escaped)
            index += 1
            continue
        if byte == 0x28:
            depth += 1
            result.append(byte)
            index += 1
            continue
        if byte == 0x29:
            depth -= 1
            if depth == 0:
                index += 1
                break
            result.append(byte)
            index += 1
            continue
        result.append(byte)
        index += 1
    return bytes(result), index


def _parse_hex_string(data: bytes, start: int) -> tuple[bytes, int]:
    end = data.find(b">", start)
    if end < 0:
        end = len(data)
    raw = re.sub(rb"\s+", b"", data[start:end])
    if len(raw) % 2:
        raw += b"0"
    try:
        value = bytes.fromhex(raw.decode("ascii", "ignore"))
    except ValueError:
        value = b""
    return value, min(end + 1, len(data))


def _operand_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return ""


def _extract_content_text(content: bytes, max_chars: int) -> tuple[str, bool]:
    output: list[str] = []
    total = 0
    operands: list[Any] = []
    index = 0
    length = len(content)

    def append_text(text: str) -> bool:
        nonlocal total
        if not text:
            return True
        output.append(text)
        total += len(text)
        return total <= max_chars

    def append_break(kind: str) -> None:
        if output:
            previous = output[-1][-1:] if output[-1] else ""
            if kind == "newline" and previous not in ("", "\n"):
                output.append("\n")
            elif kind == "space" and previous not in ("", " ", "\n", "\t"):
                output.append(" ")

    while index < length:
        byte = content[index]
        if byte in WHITESPACE:
            index += 1
            continue
        if byte == 0x25:
            newline = content.find(b"\n", index)
            index = length if newline < 0 else newline + 1
            continue
        if byte == 0x28:
            value, index = _parse_literal_string(content, index + 1)
            operands.append(_decode_pdf_bytes(value))
            continue
        if byte == 0x3C:
            if index + 1 < length and content[index + 1] == 0x3C:
                depth = 2
                cursor = index + 2
                while cursor < length and depth > 0:
                    if content[cursor : cursor + 2] == b"<<":
                        depth += 2
                        cursor += 2
                    elif content[cursor : cursor + 2] == b">>":
                        depth -= 2
                        cursor += 2
                    else:
                        cursor += 1
                index = cursor
                operands.append(None)
                continue
            value, index = _parse_hex_string(content, index + 1)
            operands.append(_decode_pdf_bytes(value))
            continue
        if byte == 0x5B:
            index += 1
            array_items: list[Any] = []
            while index < length and content[index] != 0x5D:
                current = content[index]
                if current in WHITESPACE:
                    index += 1
                    continue
                if current == 0x28:
                    value, index = _parse_literal_string(content, index + 1)
                    array_items.append(_decode_pdf_bytes(value))
                    continue
                if current == 0x3C:
                    value, index = _parse_hex_string(content, index + 1)
                    array_items.append(_decode_pdf_bytes(value))
                    continue
                if current in b"+-.0123456789":
                    match = re.match(rb"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", content[index:])
                    if match:
                        token = match.group(0)
                        try:
                            array_items.append(float(token))
                        except ValueError:
                            array_items.append(0.0)
                        index += len(token)
                        continue
                index += 1
            index += 1
            operands.append(("array", array_items))
            continue
        if byte in b"+-.0123456789":
            match = re.match(rb"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", content[index:])
            if match:
                token = match.group(0)
                try:
                    operands.append(float(token))
                except ValueError:
                    operands.append(0.0)
                index += len(token)
                continue
            index += 1
            continue
        if byte == 0x2F:
            index += 1
            while index < length and content[index] not in WHITESPACE and content[index] not in b"()<>[]{}/%":
                index += 1
            operands.append(None)
            continue
        if (0x41 <= byte <= 0x5A) or (0x61 <= byte <= 0x7A) or byte in (0x2A, 0x27, 0x22):
            start = index
            while index < length and (
                (0x41 <= content[index] <= 0x5A)
                or (0x61 <= content[index] <= 0x7A)
                or (0x30 <= content[index] <= 0x39)
                or content[index] in (0x2A, 0x27, 0x22)
            ):
                index += 1
            operator = content[start:index]
            if operator == b"Tj":
                if operands:
                    if not append_text(_operand_text(operands[-1])):
                        return "".join(output), True
            elif operator == b"TJ":
                if operands and isinstance(operands[-1], tuple):
                    for item in operands[-1][1]:
                        if isinstance(item, str):
                            if not append_text(item):
                                return "".join(output), True
                        elif isinstance(item, (int, float)) and item <= -100:
                            append_break("space")
            elif operator in (b"'", b'"'):
                append_break("newline")
                for operand in reversed(operands):
                    if isinstance(operand, str):
                        if not append_text(operand):
                            return "".join(output), True
                        break
            elif operator in TEXT_MOVE_OPS:
                if operator == b"T*":
                    append_break("newline")
                elif operator in (b"Td", b"TD"):
                    ty = operands[-1] if operands and isinstance(operands[-1], (int, float)) else 0
                    append_break("newline" if ty not in (0, 0.0) else "space")
                else:
                    append_break("space")
            if operator not in (b"BI", b"ID"):
                operands = []
            continue
        index += 1
    return "".join(output), total > max_chars


def _builtin_extract(data: bytes, max_output: int) -> tuple[str, list[str], dict[str, Any]]:
    if not data.lstrip()[:5].startswith(b"%PDF-") and b"%PDF-" not in data[:1024]:
        raise ExtractionError("PDF signature (%PDF-) is missing; file may be corrupt or mislabelled")
    if ENCRYPT_PATTERN.search(data):
        raise ExtractionError("encrypted PDFs are not supported")
    warnings: list[str] = []
    pieces: list[str] = []
    total_chars = 0
    stream_count = 0
    skipped_filters = 0
    for match in OBJECT_PATTERN.finditer(data):
        body = match.group(3)
        stream_match = STREAM_PATTERN.search(body)
        if stream_match is None:
            continue
        dictionary = body[: stream_match.start()]
        if b"/Subtype /Image" in dictionary or b"/Subtype/Image" in dictionary:
            continue
        if any(
            marker in dictionary
            for marker in (b"/Type /Metadata", b"/Type/Metadata", b"/Type /ObjStm", b"/Type/ObjStm", b"/Type /XRef", b"/Type/XRef", b"/Type /EmbeddedFile", b"/Type/EmbeddedFile")
        ):
            continue
        stream = stream_match.group(1)
        if b"/FlateDecode" in dictionary:
            if any(
                marker in dictionary
                for marker in (b"/ASCII85Decode", b"/LZWDecode", b"/RunLengthDecode", b"/DCTDecode", b"/JPXDecode", b"/CCITTFaxDecode")
            ):
                skipped_filters += 1
                continue
            try:
                decompressor = zlib.decompressobj()
                decompressed = decompressor.decompress(stream, max_output * 4 + 1)
                if len(decompressed) > max_output * 4 or decompressor.unconsumed_tail:
                    warnings.append("a compressed PDF stream exceeded the decompression limit and was skipped")
                    continue
                decompressed += decompressor.flush()
                stream = decompressed
            except zlib.error:
                warnings.append("a PDF stream could not be decompressed and was skipped")
                continue
        stream_count += 1
        text, truncated = _extract_content_text(stream, max_output - total_chars)
        if text:
            pieces.append(text)
            total_chars += len(text)
        if truncated or total_chars >= max_output:
            warnings.append(f"PDF text extraction stopped at the {max_output} character limit")
            break
    if skipped_filters:
        warnings.append(f"{skipped_filters} PDF stream(s) used unsupported filters and were skipped")
    text = "".join(pieces)
    metadata = {"engine": "builtin", "streams": stream_count, "objects": 0}
    warnings = list(dict.fromkeys(warnings))
    return text, warnings, metadata


def _pypdf_extract(data: bytes) -> tuple[str, list[str], dict[str, Any]]:
    try:
        module = importlib.import_module("pypdf")
    except ImportError as error:
        raise DependencyError(
            "pdf.engine = 'pypdf' requires the optional pypdf dependency; install datum-compiler[pdf]"
        ) from error
    try:
        reader = module.PdfReader(io.BytesIO(data), strict=False)
    except Exception as error:
        raise ExtractionError(f"pypdf could not read the PDF: {error}") from error
    if getattr(reader, "is_encrypted", False):
        raise ExtractionError("encrypted PDFs are not supported")
    warnings: list[str] = []
    pieces: list[str] = []
    for index, page in enumerate(reader.pages):
        try:
            page_text = page.extract_text() or ""
        except Exception as error:
            warnings.append(f"pypdf failed to extract page {index + 1}: {type(error).__name__}")
            continue
        if page_text:
            pieces.append(page_text)
    version = getattr(module, "__version__", "")
    if not version:
        try:
            from importlib.metadata import version as package_version

            version = package_version("pypdf")
        except Exception:
            version = "unknown"
    return "\n\n".join(pieces), warnings, {
        "engine": "pypdf",
        "engine_version": version,
        "pages": len(reader.pages),
    }


class PdfAdapter:
    name = "pdf"
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
        max_output = max(1024 * 1024, min(config.limits.max_archive_uncompressed_bytes, 8 * 1024 * 1024))
        engine = config.pdf.engine
        used_engine = engine
        warnings: list[str] = []
        metadata: dict[str, Any] = {}
        if engine in ("pypdf", "auto"):
            try:
                text, page_warnings, metadata = _pypdf_extract(data)
            except DependencyError:
                if engine == "pypdf":
                    raise
                used_engine = "builtin"
                text, warnings, metadata = _builtin_extract(data, max_output)
                warnings.append("pypdf is unavailable; the built-in PDF engine was used")
            else:
                warnings.extend(page_warnings)
        else:
            text, warnings, metadata = _builtin_extract(data, max_output)
        if not text.strip():
            warnings.append("PDF contains no extractable text; scanned PDFs require OCR (not included)")
        on_record(
            ExtractedRecord(
                ordinal=1,
                kind="text",
                locator="document",
                text=text,
                metadata=metadata,
            )
        )
        metadata = dict(metadata)
        metadata["requested_engine"] = engine
        metadata["used_engine"] = used_engine
        return ExtractionSummary(
            file_format="pdf",
            adapter=self.name,
            adapter_version=self.version,
            file_sha256=digest,
            size_bytes=size,
            encoding=None,
            records_extracted=1,
            warnings=tuple(dict.fromkeys(warnings)),
            metadata=metadata,
        )
