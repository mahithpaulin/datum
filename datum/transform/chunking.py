"""Deterministic character and token chunking."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

from ..config import ChunkingOptions
from ..errors import DependencyError


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    char_start: int | None
    char_end: int | None
    token_start: int | None = None
    token_end: int | None = None


def _character_chunks(text: str, size: int, overlap: int) -> list[Chunk]:
    if len(text) <= size:
        return [Chunk(0, text, 0, len(text))]
    stride = size - overlap
    chunks: list[Chunk] = []
    start = 0
    index = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunks.append(Chunk(index, text[start:end], start, end))
        index += 1
        if end >= len(text):
            break
        start += stride
    return chunks


def _token_chunks(text: str, tokenizer: Any, size: int, overlap: int) -> list[Chunk]:
    try:
        tokens = list(tokenizer.encode(text))
    except Exception as error:
        raise DependencyError(f"tokenizer encode() failed: {error}") from error
    if not tokens:
        return [Chunk(0, "", 0, len(text), 0, 0)]
    if len(tokens) <= size:
        return [Chunk(0, text, 0, len(text), 0, len(tokens))]
    stride = size - overlap
    chunks: list[Chunk] = []
    start = 0
    index = 0
    while start < len(tokens):
        end = min(start + size, len(tokens))
        try:
            piece = tokenizer.decode(tokens[start:end])
        except Exception as error:
            raise DependencyError(f"tokenizer decode() failed: {error}") from error
        if not isinstance(piece, str):
            raise DependencyError("tokenizer decode() must return a string")
        chunks.append(Chunk(index, piece, None, None, start, end))
        index += 1
        if end >= len(tokens):
            break
        start += stride
    return chunks


def chunk_text(text: str, options: ChunkingOptions, tokenizer: Any = None) -> list[Chunk]:
    if not options.enabled:
        return [Chunk(0, text, 0, len(text))]
    if options.unit == "chars":
        return _character_chunks(text, options.size, options.overlap)
    if tokenizer is None:
        raise DependencyError("token chunking requires a configured tokenizer")
    return _token_chunks(text, tokenizer, options.size, options.overlap)


def load_tokenizer(specification: str) -> Any:
    if not specification:
        raise DependencyError("chunking.tokenizer must name a tokenizer object")
    if ":" in specification:
        module_name, attribute = specification.rsplit(":", 1)
    else:
        module_name, attribute = specification, "tokenizer"
    try:
        module = importlib.import_module(module_name)
    except ImportError as error:
        raise DependencyError(f"cannot import tokenizer module '{module_name}': {error}") from error
    try:
        candidate = getattr(module, attribute)
    except AttributeError as error:
        raise DependencyError(
            f"tokenizer module '{module_name}' has no attribute '{attribute}'"
        ) from error
    if callable(candidate) and not (hasattr(candidate, "encode") and hasattr(candidate, "decode")):
        try:
            candidate = candidate()
        except Exception as error:
            raise DependencyError(f"tokenizer factory failed: {error}") from error
    if not (hasattr(candidate, "encode") and hasattr(candidate, "decode")):
        raise DependencyError("tokenizer must provide encode(text) and decode(tokens) methods")
    return candidate
