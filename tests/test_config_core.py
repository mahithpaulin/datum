from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from datum.config import load_config
from datum.discovery import discover
from datum.errors import ConfigError, SchemaDefinitionError
from datum.filetypes import detect_file
from datum.schema import builtin_schemas, validate_record
from datum.transform.chunking import chunk_text
from datum.config import ChunkingOptions
from datum.transform.normalize import NormalizeStats, normalize_text
from datum.build import validate_build_config


def test_unknown_config_key_fails(make_config, tmp_path):
    (tmp_path / "input").mkdir()
    path = make_config()
    path.write_text(path.read_text() + "\n[bogus]\nvalue = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(path, allow_missing_source=False)


def test_missing_source_fails(make_config, tmp_path):
    path = make_config()
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(path, allow_missing_source=False)


def test_ai_enabled_is_rejected(make_config, tmp_path):
    (tmp_path / "input").mkdir()
    path = make_config()
    path.write_text(path.read_text() + "\n[ai]\nenabled = true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not implemented"):
        load_config(path, allow_missing_source=False)


def test_invalid_split_ratios_fail(make_config, tmp_path):
    (tmp_path / "input").mkdir()
    path = make_config(split={"enabled": True, "train": 0.9, "validation": 0.2, "test": 0.1})
    with pytest.raises(ConfigError, match="must equal"):
        load_config(path, allow_missing_source=False)


def test_output_inside_source_is_rejected(make_config, tmp_path):
    (tmp_path / "input").mkdir()
    path = make_config(output="input/output")
    config = load_config(path, allow_missing_source=False)
    with pytest.raises(ConfigError, match="contain one another"):
        validate_build_config(config)


def test_truncation_requires_text_schema(make_config, tmp_path):
    (tmp_path / "input").mkdir()
    path = make_config(schema="completion", length={"oversize": "truncate"})
    config = load_config(path, allow_missing_source=False)
    with pytest.raises(ConfigError, match="only supported"):
        validate_build_config(config)


def test_builtin_text_schema_has_text_kind():
    schema = builtin_schemas()["text"]
    assert schema.kind == "text"
    assert validate_record({"text": "hello"}, schema) == []
    assert validate_record({"other": "hello"}, schema)


def test_discovery_ordering_exclusions_hidden_and_duplicates(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "b.txt").write_text("b", encoding="utf-8")
    (source / "a.txt").write_text("a", encoding="utf-8")
    (source / ".hidden.txt").write_text("hidden", encoding="utf-8")
    (source / "drop.tmp").write_text("drop", encoding="utf-8")
    nested = source / "nested"
    nested.mkdir()
    (nested / "c.md").write_text("# c", encoding="utf-8")
    path = make_config(source_options={"exclude": ["*.tmp"]})
    config = load_config(path, allow_missing_source=False)
    result = discover(config.source)
    assert [entry.relative_path for entry in result.files] == ["a.txt", "b.txt", "nested/c.md"]
    assert result.hidden == 1
    assert result.excluded == 1
    assert detect_file(result.files[0].path, config).format == "text"
    assert detect_file(result.files[2].path, config).format == "markdown"


def test_discovery_skips_hardlink_duplicate(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    first = source / "a.txt"
    first.write_text("same", encoding="utf-8")
    second = source / "b.txt"
    try:
        os.symlink(first, second)
    except OSError:
        pytest.skip("symlinks are unavailable")
    path = make_config(source_options={"follow_symlinks": True})
    config = load_config(path, allow_missing_source=False)
    result = discover(config.source)
    assert len(result.files) == 1
    assert result.duplicate_targets_skipped == 1


def test_character_chunking_overlap_and_normalization():
    options = ChunkingOptions(enabled=True, unit="chars", size=10, overlap=3)
    chunks = chunk_text("abcdefghijklmnopqrstuvwxyz", options)
    assert chunks[0].text == "abcdefghij"
    assert chunks[1].text == "hijklmnopq"
    assert all(chunk.char_start is not None for chunk in chunks)
    normalized, stats = normalize_text("a\r\nb\x00c  \n", load_normalize())
    assert normalized == "a\nbc"
    assert stats.line_endings_converted == 1
    assert stats.control_chars_removed == 1


def test_control_character_normalization_is_exact():
    from datum.config import NormalizeOptions

    options = NormalizeOptions(
        line_endings="preserve",
        strip_control_chars=True,
        strip_outer_whitespace=False,
        strip_trailing_whitespace=False,
    )
    normalized, stats = normalize_text("\x00a\rb\tc\n\x7f\x85d", options)
    assert normalized == "a\rb\tc\nd"
    assert stats.control_chars_removed == 3


def load_normalize():
    from datum.config import NormalizeOptions

    return NormalizeOptions(
        line_endings="lf",
        strip_control_chars=True,
        strip_outer_whitespace=True,
        strip_trailing_whitespace=True,
    )


def test_custom_schema_rejects_nested_additional_properties(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "rows.jsonl").write_text(
        json.dumps({"title": "A", "meta": {"score": 1, "extra": True}}) + "\n",
        encoding="utf-8",
    )
    schema = tmp_path / "schema.json"
    schema.write_text(
        json.dumps(
            {
                "name": "article",
                "version": 1,
                "fields": {
                    "title": {"type": "string", "required": True},
                    "meta": {
                        "type": "object",
                        "required": True,
                        "properties": {"score": {"type": "integer", "required": True}},
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    path = make_config(schema="custom", schema_file=schema)
    config = load_config(path, allow_missing_source=False)
    from datum.schema import load_schema

    loaded = load_schema(config)
    assert loaded.spec.kind == "custom"
    problems = validate_record({"title": "A", "meta": {"score": 1, "extra": True}}, loaded.spec)
    assert any("additional property" in problem for problem in problems)


def test_unknown_custom_schema_key_fails(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({"name": "x", "version": 1, "fields": {"a": {"type": "string"}}, "bogus": 1}))
    path = make_config(schema="custom", schema_file=schema)
    config = load_config(path, allow_missing_source=False)
    from datum.schema import load_schema

    with pytest.raises(SchemaDefinitionError, match="unknown custom schema"):
        load_schema(config)
