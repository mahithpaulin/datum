"""Ingestion adapter registry."""

from __future__ import annotations

from typing import Mapping

from .base import Adapter, ExtractedRecord, ExtractionSummary, RecordIssue
from .docx import DocxAdapter
from .pdf import PdfAdapter
from .structured import DelimitedAdapter, JsonAdapter, JsonlAdapter
from .text import TextAdapter

_ADAPTERS: dict[str, Adapter] = {
    adapter.name: adapter
    for adapter in (
        TextAdapter(),
        DelimitedAdapter(),
        JsonAdapter(),
        JsonlAdapter(),
        PdfAdapter(),
        DocxAdapter(),
    )
}


def get_adapter(name: str) -> Adapter:
    try:
        return _ADAPTERS[name]
    except KeyError as error:
        raise ValueError(f"no ingestion adapter registered as '{name}'") from error


def adapter_registry() -> Mapping[str, Adapter]:
    return dict(_ADAPTERS)


__all__ = [
    "Adapter",
    "ExtractedRecord",
    "ExtractionSummary",
    "RecordIssue",
    "get_adapter",
    "adapter_registry",
]
