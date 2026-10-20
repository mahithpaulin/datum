"""Input format detection using extensions, magic bytes, and content signals."""

from __future__ import annotations

import io
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .config import Config

HEAD_BYTES = 64 * 1024

TEXT_EXTENSIONS = {".txt", ".text", ".log", ".rst", ".tex"}
MARKDOWN_EXTENSIONS = {".md", ".markdown", ".mdown", ".mkd"}
CSV_EXTENSIONS = {".csv"}
TSV_EXTENSIONS = {".tsv", ".tab"}
JSON_EXTENSIONS = {".json"}
JSONL_EXTENSIONS = {".jsonl", ".ndjson", ".jsonlines"}
PDF_EXTENSIONS = {".pdf"}
DOCX_EXTENSIONS = {".docx"}

CODE_EXTENSIONS = {
    ".py",
    ".pyi",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".cxx",
    ".hpp",
    ".hh",
    ".rs",
    ".go",
    ".rb",
    ".php",
    ".cs",
    ".kt",
    ".kts",
    ".swift",
    ".scala",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".sql",
    ".r",
    ".jl",
    ".lua",
    ".pl",
    ".pm",
    ".m",
    ".mm",
    ".vue",
    ".svelte",
    ".dart",
    ".ex",
    ".exs",
    ".erl",
    ".hrl",
    ".hs",
    ".lhs",
    ".clj",
    ".cljs",
    ".cljc",
    ".groovy",
    ".gradle",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".yaml",
    ".yml",
    ".dockerfile",
    ".makefile",
    ".cmake",
    ".tf",
    ".hcl",
    ".proto",
    ".graphql",
    ".gql",
    ".sol",
    ".asm",
    ".s",
    ".vb",
    ".fs",
    ".fsx",
}

UNSUPPORTED_HINTS = {
    ".doc": "legacy binary .doc is not supported; convert it to .docx",
    ".xls": "legacy binary .xls is not supported; convert it to .xlsx or CSV",
    ".xlsx": "XLSX is not supported in DATUM 0.1.0; export the sheet to CSV",
    ".parquet": "Parquet is not supported in DATUM 0.1.0; export it to JSONL or CSV first",
    ".html": "HTML extraction is not supported in DATUM 0.1.0; convert it to text or Markdown",
    ".htm": "HTML extraction is not supported in DATUM 0.1.0; convert it to text or Markdown",
    ".xml": "XML extraction is not supported in DATUM 0.1.0; convert it to text or JSON",
    ".zip": "archives are not extracted; select the source directory directly",
    ".tar": "archives are not extracted; select the source directory directly",
    ".gz": "compressed files are not extracted; decompress them before building",
    ".7z": "archives are not extracted; select the source directory directly",
    ".png": "image inputs are unsupported; DATUM 0.1.0 does not perform OCR",
    ".jpg": "image inputs are unsupported; DATUM 0.1.0 does not perform OCR",
    ".jpeg": "image inputs are unsupported; DATUM 0.1.0 does not perform OCR",
    ".gif": "image inputs are unsupported; DATUM 0.1.0 does not perform OCR",
    ".webp": "image inputs are unsupported; DATUM 0.1.0 does not perform OCR",
    ".mp3": "audio inputs are unsupported",
    ".mp4": "video inputs are unsupported",
    ".wav": "audio inputs are unsupported",
}

BINARY_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "PNG image"),
    (b"\xff\xd8\xff", "JPEG image"),
    (b"GIF87a", "GIF image"),
    (b"GIF89a", "GIF image"),
    (b"\x7fELF", "ELF binary"),
    (b"\x1f\x8b", "gzip stream"),
    (b"BZh", "bzip2 stream"),
    (b"\xfd7zXZ\x00", "xz stream"),
    (b"SQLite format 3\x00", "SQLite database"),
)


@dataclass(frozen=True)
class Detection:
    format: str
    adapter: str
    supported: bool
    is_binary: bool
    extension: str
    encoding_hint: str | None = None
    reason: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "format": self.format,
            "adapter": self.adapter,
            "supported": self.supported,
            "binary": self.is_binary,
            "extension": self.extension,
            "encoding_hint": self.encoding_hint,
            "reason": self.reason,
            "error": self.error,
        }


def _bom_encoding(head: bytes) -> str | None:
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if head.startswith(b"\xff\xfe\x00\x00") or head.startswith(b"\x00\x00\xfe\xff"):
        return "utf-32"
    if head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff"):
        return "utf-16"
    return None


def _looks_binary(head: bytes, boms: str | None) -> bool:
    if not head:
        return False
    if boms is not None:
        return False
    if b"\x00" in head:
        return True
    for magic, _label in BINARY_MAGIC:
        if head.startswith(magic):
            return True
    sample = head.decode("utf-8", errors="replace")
    if not sample:
        return False
    bad = 0
    for character in sample:
        if character == "\ufffd":
            bad += 1
            continue
        if character in "\n\r\t\f\b":
            continue
        if unicodedata.category(character) == "Cc":
            bad += 1
    return bad / len(sample) > 0.05


def _zip_is_docx(head: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(head)) as archive:
            names = set(archive.namelist())
        return "[Content_Types].xml" in names and "word/document.xml" in names
    except (zipfile.BadZipFile, OSError):
        return False


def _unsupported(extension: str, reason: str, *, binary: bool = False, error: str | None = None) -> Detection:
    return Detection(
        format="unsupported",
        adapter="",
        supported=False,
        is_binary=binary,
        extension=extension,
        reason=reason,
        error=error,
    )


def detect_file(path: Path, config: Config) -> Detection:
    extension = path.suffix.lower()
    try:
        with path.open("rb") as handle:
            head = handle.read(HEAD_BYTES)
    except OSError as error:
        return _unsupported(extension, "cannot read file for type detection", error=str(error))

    boms = _bom_encoding(head)
    encoding_hint = boms
    if config.source.encoding not in ("auto", ""):
        encoding_hint = config.source.encoding

    if head.startswith(b"%PDF-"):
        return Detection(
            format="pdf",
            adapter="pdf",
            supported=True,
            is_binary=True,
            extension=extension,
            reason="PDF signature detected",
        )

    if head.startswith(b"PK\x03\x04"):
        if _zip_is_docx(head):
            return Detection(
                format="docx",
                adapter="docx",
                supported=True,
                is_binary=True,
                extension=extension,
                reason="ZIP container with DOCX structure",
            )
        if extension in DOCX_EXTENSIONS:
            return Detection(
                format="docx",
                adapter="docx",
                supported=True,
                is_binary=True,
                extension=extension,
                reason="DOCX extension; container validation deferred to the adapter",
            )
        return _unsupported(extension, "ZIP archive is not a DOCX document", binary=True)

    if extension in PDF_EXTENSIONS:
        return Detection(
            format="pdf",
            adapter="pdf",
            supported=True,
            is_binary=True,
            extension=extension,
            reason="PDF extension; missing %PDF signature will be reported by the adapter",
        )

    binary = _looks_binary(head, boms)
    if binary:
        hint = UNSUPPORTED_HINTS.get(extension)
        return _unsupported(
            extension,
            hint or "binary content is not interpreted as text",
            binary=True,
        )

    known = (
        extension in TEXT_EXTENSIONS
        or extension in MARKDOWN_EXTENSIONS
        or extension in CSV_EXTENSIONS
        or extension in TSV_EXTENSIONS
        or extension in JSON_EXTENSIONS
        or extension in JSONL_EXTENSIONS
        or extension in CODE_EXTENSIONS
        or extension in DOCX_EXTENSIONS
    )

    if extension in TEXT_EXTENSIONS:
        return Detection("text", "text", True, False, extension, encoding_hint, "text extension")
    if extension in MARKDOWN_EXTENSIONS:
        return Detection("markdown", "text", True, False, extension, encoding_hint, "Markdown extension")
    if extension in CODE_EXTENSIONS:
        return Detection("code", "text", True, False, extension, encoding_hint, "source-code extension")
    if extension in CSV_EXTENSIONS:
        return Detection("csv", "delimited", True, False, extension, encoding_hint, "CSV extension")
    if extension in TSV_EXTENSIONS:
        return Detection("tsv", "delimited", True, False, extension, encoding_hint, "TSV extension")
    if extension in JSON_EXTENSIONS:
        return Detection("json", "json", True, False, extension, encoding_hint, "JSON extension")
    if extension in JSONL_EXTENSIONS:
        return Detection("jsonl", "jsonl", True, False, extension, encoding_hint, "JSONL extension")
    if extension in DOCX_EXTENSIONS:
        return Detection(
            "docx",
            "docx",
            True,
            False,
            extension,
            None,
            "DOCX extension; container validation deferred to the adapter",
        )

    if extension in UNSUPPORTED_HINTS:
        return _unsupported(extension, UNSUPPORTED_HINTS[extension])

    if config.source.detect_content and head.lstrip().startswith(b"#!"):
        return Detection(
            "code",
            "text",
            True,
            False,
            extension,
            encoding_hint,
            "shebang detected in an otherwise unknown text file",
        )

    if not known and config.source.text_fallback and not binary:
        return Detection(
            "text",
            "text",
            True,
            False,
            extension,
            encoding_hint,
            "unknown text extension accepted by source.text_fallback",
        )

    return _unsupported(
        extension,
        "unsupported file type; supported: TXT, Markdown, CSV/TSV, JSON, JSONL, PDF, DOCX, and common source-code extensions",
    )


def format_breakdown(detections: list[Detection]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for detection in detections:
        label = detection.format if detection.supported else "unsupported"
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items()))
