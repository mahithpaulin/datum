from __future__ import annotations

import json

from datum.build import run_build
from datum.config import load_config
from datum.verify import verify_build


def build_sample(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "a.txt").write_text("A verified record with provenance and hashes.\n", encoding="utf-8")
    config = load_config(make_config(), allow_missing_source=False)
    return run_build(config)


def test_verify_detects_tampered_dataset(make_config, tmp_path):
    result = build_sample(make_config, tmp_path)
    assert verify_build(result.output_directory).ok
    dataset = result.output_directory / "dataset.jsonl"
    dataset.write_text(
        dataset.read_text(encoding="utf-8") + '{"text":"injected"}\n',
        encoding="utf-8",
    )
    report = verify_build(result.output_directory)
    assert not report.ok
    assert any(check["name"] == "output_sha256" and not check["passed"] for check in report.checks)


def test_verify_detects_missing_sidecar(make_config, tmp_path):
    result = build_sample(make_config, tmp_path)
    (result.output_directory / "provenance.jsonl").unlink()
    report = verify_build(result.output_directory)
    assert not report.ok
    assert any("provenance.jsonl" in error for error in report.errors)


def test_verify_requires_inline_field(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "a.txt").write_text("Inline provenance record for verification.\n", encoding="utf-8")
    config = load_config(make_config(provenance="inline"), allow_missing_source=False)
    result = run_build(config)
    dataset = result.output_directory / "dataset.jsonl"
    payload = json.loads(dataset.read_text(encoding="utf-8"))
    payload.pop("_datum")
    dataset.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    report = verify_build(result.output_directory)
    assert not report.ok


def test_verify_missing_manifest_reports_failure(tmp_path):
    report = verify_build(tmp_path)
    assert not report.ok
    assert report.checks[0]["name"] == "manifest_readable"


def test_verify_rejects_unsupported_manifest_version(make_config, tmp_path):
    result = build_sample(make_config, tmp_path)
    manifest_path = result.output_directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["manifest_schema_version"] = 999
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report = verify_build(result.output_directory)
    assert not report.ok
    assert "unsupported manifest schema" in report.errors[0]


def test_verify_detects_unsafe_output_path(make_config, tmp_path):
    result = build_sample(make_config, tmp_path)
    manifest_path = result.output_directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["outputs"][0]["path"] = "../../escape.jsonl"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report = verify_build(result.output_directory)
    assert not report.ok
    assert any("unsafe relative path" in error for error in report.errors)
