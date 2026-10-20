from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from datum.build import run_build
from datum.config import load_config
from datum.errors import BuildError
from datum.util import sha256_file
from datum.verify import verify_build


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def load(path, **overrides):
    config = load_config(path, allow_missing_source=False)
    if overrides:
        config = config.with_overrides(**overrides)
    return config


def test_text_build_dedup_sidecar_and_determinism(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    text = "A sufficiently long record about deterministic datasets.\n"
    (source / "a.txt").write_text(text, encoding="utf-8")
    (source / "b.txt").write_text(text, encoding="utf-8")
    (source / "c.txt").write_text("A different record with useful evidence.\n", encoding="utf-8")
    config = load(make_config())
    first = run_build(config)
    assert first.status == "passed"
    assert first.records["source_records_extracted"] == 3
    assert first.records["exported"] == 2
    assert first.records["duplicates"] == 1
    assert first.records["rejected_total"] == 1
    assert first.splits == {"dataset": 2}
    first_identity = first.manifest["output_identity"]
    first_sha, _ = sha256_file(first.output_directory / "dataset.jsonl")
    sidecar = read_jsonl(first.output_directory / "provenance.jsonl")
    rejected = read_jsonl(first.output_directory / "rejected.jsonl")
    assert len(sidecar) == 2
    assert len({item["record_id"] for item in sidecar}) == 2
    assert rejected[0]["rule"] == "duplicate"
    assert verify_build(first.output_directory, check_sources=True).ok

    second = run_build(load(make_config(), cache_enabled=False))
    second_sha, _ = sha256_file(second.output_directory / "dataset.jsonl")
    assert second.manifest["output_identity"] == first_identity
    assert second.build_id == first.build_id
    assert second_sha == first_sha


def test_cache_hits_and_invalidation(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    path = source / "a.txt"
    path.write_text("The first version is long enough for the cache test.\n", encoding="utf-8")
    config = load(make_config())
    first = run_build(config)
    assert first.cache["misses"] == 1
    assert first.cache["writes"] == 1
    second = run_build(config)
    assert second.cache["hits"] == 1
    assert second.cache["misses"] == 0
    path.write_text("A completely different and still long enough version.\n", encoding="utf-8")
    third = run_build(config)
    assert third.cache["misses"] == 1
    assert third.cache["writes"] == 1
    assert third.records["exported"] == 1
    assert verify_build(third.output_directory).ok


def test_strict_mode_malformed_input_and_cache_do_not_bypass(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "rows.jsonl").write_text(
        '{"text":"This valid record is long enough."}\n'
        '{"text": "broken"\n',
        encoding="utf-8",
    )
    permissive = load(make_config(mapping={"text": "text"}))
    first = run_build(permissive)
    assert first.status == "passed"
    assert first.records["malformed_input"] == 1
    assert first.records["exported"] == 1
    assert read_jsonl(first.output_directory / "rejected.jsonl")[0]["rule"] == "malformed_input"
    strict_path = make_config(
        mapping={"text": "text"},
        errors={"policy": "strict"},
        filename="strict.toml",
    )
    with pytest.raises(BuildError, match="strict mode"):
        run_build(load(strict_path))
    assert json.loads((tmp_path / "out" / "current" / "manifest.json").read_text())["build"]["status"] == "complete"


def test_strict_mode_fails_on_file_error(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "bad.pdf").write_bytes(b"not a pdf")
    path = make_config(errors={"policy": "strict"})
    with pytest.raises(BuildError, match="strict mode"):
        run_build(load(path))
    assert not (tmp_path / "out" / "current").exists()


def test_quality_failure_publishes_to_failed_and_preserves_current(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "a.txt").write_text("A valid record for the first successful build.\n", encoding="utf-8")
    first = run_build(load(make_config()))
    assert first.status == "passed"
    failing_path = make_config(quality={"min_exported_records": 99}, filename="failing.toml")
    failed = run_build(load(failing_path))
    assert failed.status == "quality_failed"
    assert "failed" in failed.output_directory.parts
    assert (tmp_path / "out" / "current" / "manifest.json").is_file()
    current = json.loads((tmp_path / "out" / "current" / "manifest.json").read_text())
    assert current["build"]["status"] == "complete"
    assert verify_build(failed.output_directory).ok
    assert json.loads((failed.output_directory / "manifest.json").read_text())["build"]["status"] == "quality_failed"


def test_inline_provenance_and_verify_tamper(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "a.txt").write_text("Inline provenance must remain verifiable.\n", encoding="utf-8")
    config = load(make_config(provenance="inline"))
    result = run_build(config)
    assert result.status == "passed"
    assert not (result.output_directory / "provenance.jsonl").exists()
    payload = read_jsonl(result.output_directory / "dataset.jsonl")[0]
    assert payload["_datum"]["record_id"]
    assert verify_build(result.output_directory).ok
    dataset = result.output_directory / "dataset.jsonl"
    dataset.write_text(dataset.read_text(encoding="utf-8").replace("verifiable", "modified"), encoding="utf-8")
    report = verify_build(result.output_directory)
    assert not report.ok
    assert any("output_sha256" in error for error in report.errors)


def test_provenance_off(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "a.txt").write_text("No provenance output in this mode.\n", encoding="utf-8")
    result = run_build(load(make_config(provenance="off")))
    assert result.status == "passed"
    assert not (result.output_directory / "provenance.jsonl").exists()
    assert result.manifest["sidecar"] is None
    assert verify_build(result.output_directory).ok


def test_truncation_is_reported_and_verified(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "long.txt").write_text("x" * 120, encoding="utf-8")
    config = load(make_config(length={"min_chars": 1, "max_chars": 30, "oversize": "truncate"}))
    result = run_build(config)
    assert result.status == "passed"
    payload = read_jsonl(result.output_directory / "dataset.jsonl")[0]
    assert payload["text"] == "x" * 30
    assert result.records["truncated"] == 1
    assert result.quality["transformation"]["truncated_records"] == 1
    assert verify_build(result.output_directory).ok


def test_split_counts_are_deterministic(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    for index in range(24):
        (source / f"{index:02d}.txt").write_text(
            f"Record number {index} contains enough distinct content for splitting.\n",
            encoding="utf-8",
        )
    config = load(make_config(split={"enabled": True, "train": 0.5, "validation": 0.25, "test": 0.25}))
    first = run_build(config)
    assert first.status == "passed"
    assert sum(first.splits.values()) == 24
    assert set(first.splits) == {"train", "validation", "test"}
    for name in ("train", "validation", "test"):
        assert (first.output_directory / f"{name}.jsonl").is_file()
    second = run_build(config)
    assert second.splits == first.splits
    assert second.manifest["output_identity"] == first.manifest["output_identity"]


def test_custom_schema_rejection_is_logged(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "rows.jsonl").write_text(
        '{"title":"Good row","count":2}\n{"title":"Bad count","count":"two"}\n',
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
                    "count": {"type": "integer", "required": True},
                },
            }
        ),
        encoding="utf-8",
    )
    config = load(make_config(schema="custom", schema_file=schema))
    result = run_build(config)
    assert result.status == "passed"
    assert result.records["exported"] == 1
    assert result.records["schema_rejected"] == 1
    rejected = read_jsonl(result.output_directory / "rejected.jsonl")
    assert rejected[0]["rule"] == "schema_invalid"
    assert result.manifest["schema"]["name"] == "article"
    assert verify_build(result.output_directory).ok


def test_length_filters_and_empty_source_pass_with_zero_minimum(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "empty.txt").write_text("", encoding="utf-8")
    (source / "short.txt").write_text("tiny", encoding="utf-8")
    (source / "long.txt").write_text("y" * 50, encoding="utf-8")
    config = load(
        make_config(
            length={"min_chars": 10, "max_chars": 20},
            quality={"min_exported_records": 0},
        )
    )
    result = run_build(config)
    assert result.status == "passed"
    assert result.records["exported"] == 0
    assert result.records["empty"] == 1
    assert result.records["too_short"] == 1
    assert result.records["too_long"] == 1
    assert len(read_jsonl(result.output_directory / "rejected.jsonl")) == 3
    assert verify_build(result.output_directory).ok


def test_empty_source_with_zero_minimum(make_config, tmp_path):
    (tmp_path / "input").mkdir()
    result = run_build(load(make_config(quality={"min_exported_records": 0})))
    assert result.status == "passed"
    assert result.records["exported"] == 0
    assert (result.output_directory / "dataset.jsonl").is_file()
    assert verify_build(result.output_directory).ok


def test_manifest_input_hashes_and_source_verification(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "a.txt").write_text("A source file whose hash is in the manifest.\n", encoding="utf-8")
    result = run_build(load(make_config()))
    manifest = json.loads((result.output_directory / "manifest.json").read_text())
    assert manifest["inputs"][0]["sha256"] == hashlib.sha256(
        (source / "a.txt").read_bytes()
    ).hexdigest()
    assert verify_build(result.output_directory, check_sources=True).ok
    (source / "a.txt").write_text("changed content long enough for the same file path.\n", encoding="utf-8")
    assert not verify_build(result.output_directory, check_sources=True).ok
