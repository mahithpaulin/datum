from __future__ import annotations

import json
from pathlib import Path

import pytest


def toml_literal(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_literal(item) for item in value) + "]"
    raise TypeError(type(value))


@pytest.fixture
def make_config(tmp_path):
    def make(
        *,
        source: str = "input",
        output: str = "out",
        schema: str = "text",
        schema_file: Path | None = None,
        mapping: dict[str, str] | None = None,
        dedup: bool = True,
        split: dict | None = None,
        provenance: str = "sidecar",
        quality: dict | None = None,
        errors: dict | None = None,
        source_options: dict | None = None,
        chunking: dict | None = None,
        length: dict | None = None,
        cache: bool = True,
        cache_dir: Path | None = None,
        workers: int = 1,
        completion_separator: str | None = None,
        filename: str = "datum.toml",
    ) -> Path:
        source_options = {"path": source, **(source_options or {})}
        quality_options = {
            "min_exported_records": 1,
            "max_file_error_rate": 1.0,
            "max_schema_rejection_rate": 1.0,
            "max_duplicate_rate": 1.0,
            **(quality or {}),
        }
        error_options = {"policy": "permissive", "unsupported": "skip", **(errors or {})}
        split_options = {
            "enabled": False,
            "seed": 42,
            "train": 0.8,
            "validation": 0.1,
            "test": 0.1,
            **(split or {}),
        }
        chunking_options = {
            "enabled": False,
            "unit": "chars",
            "size": 4000,
            "overlap": 0,
            **(chunking or {}),
        }
        length_options = {
            "min_chars": 1,
            "max_chars": 1_000_000,
            "oversize": "reject",
            **(length or {}),
        }
        cache_lines = ["[cache]", f"enabled = {toml_literal(cache)}"]
        if cache_dir is not None:
            cache_lines.append(f"dir = {toml_literal(str(cache_dir))}")
        lines = []
        if completion_separator is not None:
            lines.append(f"completion_separator = {toml_literal(completion_separator)}")
            lines.append("")
        lines.extend(
            [
                "[datum]",
                "config_version = 1",
                "",
                "[source]",
            ]
        )
        for key, value in source_options.items():
            lines.append(f"{key} = {toml_literal(value)}")
        lines.extend(
            [
                "",
                "[schema]",
                f"name = {toml_literal(schema)}",
            ]
        )
        if schema_file is not None:
            lines.append(f"file = {toml_literal(str(schema_file))}")
        lines.extend(
            [
                "",
                "[normalize]",
                'line_endings = "lf"',
                "strip_outer_whitespace = true",
                "",
                "[chunking]",
            ]
        )
        for key, value in chunking_options.items():
            lines.append(f"{key} = {toml_literal(value)}")
        lines.extend(["", "[length]"])
        for key, value in length_options.items():
            lines.append(f"{key} = {toml_literal(value)}")
        if mapping:
            lines.extend(["", "[mapping.fields]"])
            for key, value in mapping.items():
                lines.append(f"{toml_literal(key)} = {toml_literal(value)}")
        lines.extend(["", "[dedup]", f"enabled = {toml_literal(dedup)}", 'index = "memory"'])
        lines.extend(["", "[split]"])
        for key, value in split_options.items():
            lines.append(f"{key} = {toml_literal(value)}")
        lines.extend(["", "[provenance]", f"mode = {toml_literal(provenance)}"])
        lines.extend(["", "[output]", f"dir = {toml_literal(output)}"])
        lines.extend(["", "[quality]"])
        for key, value in quality_options.items():
            lines.append(f"{key} = {toml_literal(value)}")
        lines.extend(["", "[errors]"])
        for key, value in error_options.items():
            lines.append(f"{key} = {toml_literal(value)}")
        lines.extend(cache_lines)
        lines.extend(["", "[runtime]", f"workers = {workers}", "progress = false"])
        path = tmp_path / filename
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    return make
