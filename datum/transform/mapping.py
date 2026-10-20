"""Mapping extracted records into target dataset schemas."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..config import Config
from ..ingest.base import ExtractedRecord
from ..schema import LoadedSchema, record_content_length, resolve_mapping
from .chunking import chunk_text
from .normalize import NormalizeStats, normalize_text


class MappingFailure(ValueError):
    pass


@dataclass(frozen=True)
class Draft:
    data: dict[str, Any]
    length: int
    chunk_index: int = 0
    chunk_count: int = 1
    char_start: int | None = None
    char_end: int | None = None
    token_start: int | None = None
    token_end: int | None = None
    flags: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "data": self.data,
            "length": self.length,
            "chunk_index": self.chunk_index,
            "chunk_count": self.chunk_count,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "token_start": self.token_start,
            "token_end": self.token_end,
            "flags": dict(self.flags),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Draft":
        return cls(
            data=value["data"],
            length=int(value["length"]),
            chunk_index=int(value.get("chunk_index", 0)),
            chunk_count=int(value.get("chunk_count", 1)),
            char_start=value.get("char_start"),
            char_end=value.get("char_end"),
            token_start=value.get("token_start"),
            token_end=value.get("token_end"),
            flags=value.get("flags") or {},
        )


def get_path(data: Any, path: str) -> Any:
    if path in ("$", ""):
        return data
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                raise MappingFailure(f"source field not found: {path}")
            current = current[part]
        elif isinstance(current, list):
            try:
                index = int(part)
            except ValueError as error:
                raise MappingFailure(f"array path segment must be an integer: {path}") from error
            if index < 0 or index >= len(current):
                raise MappingFailure(f"array index out of range in path: {path}")
            current = current[index]
        else:
            raise MappingFailure(f"cannot descend through a scalar field in path: {path}")
    return current


def _normalize_string(value: str, config: Config, stats: NormalizeStats) -> str:
    normalized, changes = normalize_text(value, config.normalize)
    stats.add(changes)
    return normalized


def _normalize_recursive(value: Any, config: Config, stats: NormalizeStats) -> Any:
    if isinstance(value, str):
        return _normalize_string(value, config, stats)
    if isinstance(value, list):
        return [_normalize_recursive(item, config, stats) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_recursive(item, config, stats) for key, item in value.items()}
    return value


def _content_from_data(record: ExtractedRecord, path: str, config: Config, stats: NormalizeStats) -> str:
    if path in ("@text", "$"):
        if isinstance(record.data, str):
            value = record.data
        elif path == "@text" and record.text is not None:
            value = record.text
        else:
            raise MappingFailure(
                "text mapping requires a string source value; use mapping.fields.text to select a field"
            )
    else:
        value = get_path(record.data, path)
    if not isinstance(value, str):
        raise MappingFailure(f"mapped text field '{path}' must contain a string")
    return _normalize_string(value, config, stats)


def _field_value(
    record: ExtractedRecord,
    output_field: str,
    path: str,
    required: bool,
    config: Config,
    stats: NormalizeStats,
    preserve_string: bool = False,
) -> tuple[bool, Any]:
    try:
        value = get_path(record.data, path)
    except MappingFailure:
        if required:
            raise MappingFailure(
                f"required field '{output_field}' is missing at source path '{path}'"
            ) from None
        return False, None
    if preserve_string:
        return True, value
    return True, _normalize_recursive(value, config, stats)


def _structured_required(record: ExtractedRecord, schema: LoadedSchema) -> None:
    if record.kind != "data":
        names = ", ".join(schema.spec.fields)
        raise MappingFailure(
            f"schema '{schema.spec.name}' requires structured records with field(s): {names}; "
            "plain-text inputs cannot supply them"
        )


def _chat_record(
    record: ExtractedRecord,
    config: Config,
    schema: LoadedSchema,
    mapping: Mapping[str, str],
    stats: NormalizeStats,
) -> dict[str, Any]:
    _structured_required(record, schema)
    path = mapping.get("messages", "messages")
    try:
        messages = get_path(record.data, path)
    except MappingFailure:
        raise MappingFailure(f"chat record is missing messages at source path '{path}'") from None
    if not isinstance(messages, list):
        raise MappingFailure("chat messages must be an array")
    copied: list[Any] = []
    for message in messages:
        if isinstance(message, dict):
            item = dict(message)
            if isinstance(item.get("content"), str):
                item["content"] = _normalize_string(item["content"], config, stats)
            copied.append(item)
        else:
            copied.append(message)
    return {"messages": copied}


def build_drafts(
    record: ExtractedRecord,
    config: Config,
    schema: LoadedSchema,
    mapping: Mapping[str, str],
    persist_stats: NormalizeStats,
    tokenizer: Any = None,
) -> list[Draft]:
    kind = schema.spec.kind
    resolved = resolve_mapping(schema, mapping)
    if kind == "text":
        if record.kind == "text":
            path = resolved.get("text", "@text")
            if path not in ("@text", "$"):
                raise MappingFailure(
                    f"plain-text input cannot use structured mapping path '{path}'; use '@text'"
                )
            content = _normalize_string(record.text or "", config, persist_stats)
        else:
            content = _content_from_data(record, resolved.get("text", "text"), config, persist_stats)
        chunks = chunk_text(content, config.chunking, tokenizer)
        drafts: list[Draft] = []
        for chunk in chunks:
            drafts.append(
                Draft(
                    data={"text": chunk.text},
                    length=len(chunk.text),
                    chunk_index=chunk.index,
                    chunk_count=len(chunks),
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    token_start=chunk.token_start,
                    token_end=chunk.token_end,
                    flags={"chunked": len(chunks) > 1},
                )
            )
        return drafts

    if kind == "completion":
        if record.kind == "text":
            if not config.completion_separator:
                raise MappingFailure(
                    "completion schema requires completion_separator for plain-text input"
                )
            normalized = _normalize_string(record.text or "", config, persist_stats)
            if config.completion_separator not in normalized:
                raise MappingFailure(
                    f"completion_separator '{config.completion_separator}' was not found in the input record"
                )
            prompt, completion = normalized.split(config.completion_separator, 1)
            data = {"prompt": prompt.strip(), "completion": completion.strip()}
        else:
            data = {}
            _structured_required(record, schema)
            present, prompt = _field_value(
                record, "prompt", resolved.get("prompt", "prompt"), True, config, persist_stats
            )
            data["prompt"] = prompt
            present, completion = _field_value(
                record, "completion", resolved.get("completion", "completion"), True, config, persist_stats
            )
            data["completion"] = completion
            if not isinstance(data["prompt"], str) or not isinstance(data["completion"], str):
                raise MappingFailure("completion fields must be strings")
        return [Draft(data=data, length=record_content_length(data, schema.spec))]

    if kind == "chat":
        data = _chat_record(record, config, schema, resolved, persist_stats)
        return [Draft(data=data, length=record_content_length(data, schema.spec))]

    _structured_required(record, schema)
    data: dict[str, Any] = {}
    for name, field_spec in schema.spec.fields.items():
        path = resolved.get(name, name)
        present, value = _field_value(
            record,
            name,
            path,
            field_spec.required,
            config,
            persist_stats,
            preserve_string=(kind == "classification" and name == "label"),
        )
        if present:
            data[name] = value
    return [Draft(data=data, length=record_content_length(data, schema.spec))]
