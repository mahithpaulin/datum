from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.generate_data import PROFILES, TIER_BYTES, generate
from benchmarks.run import main as run_benchmark
from benchmarks.verify_results import validate_report
from benchmarks.workloads import run as run_workload


def test_profile_manifest_matches_runner():
    manifest = json.loads(
        (Path(__file__).resolve().parent.parent / "benchmarks" / "profiles" / "profiles.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["schema_version"] == 1
    assert set(manifest["profiles"]) == set(PROFILES)
    assert manifest["tiers"] == TIER_BYTES


def test_generated_fixtures_are_deterministic(tmp_path):
    first = generate("many-small-text", tmp_path / "first", target_bytes=200_000, seed=7)
    second = generate("many-small-text", tmp_path / "second", target_bytes=200_000, seed=7)
    assert first["fixture_sha256"] == second["fixture_sha256"]
    assert first["input_bytes"] == second["input_bytes"]
    assert first["counts"]["files"] == first["input_files"]
    assert (tmp_path / "first" / "datum.toml").is_file()
    assert (tmp_path / "first" / "datum-workflow.toml").is_file()


@pytest.mark.parametrize("profile", ["csv-transform", "jsonl-training"])
def test_runner_parity_for_structured_profiles(profile, tmp_path):
    runs = tmp_path / "runs"
    code = run_benchmark(
        [
            "--profile",
            profile,
            "--tier",
            "A",
            "--mode",
            "equivalent",
            "--tools",
            "python",
            "--repetitions",
            "1",
            "--warmups",
            "0",
            "--target-bytes",
            "200000",
            "--runs-dir",
            str(runs),
            "--run-id",
            profile,
            "--allow-low-resource",
            "--no-verify",
        ]
    )
    assert code == 0
    report = json.loads((runs / profile / "report.json").read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["validation"]["ok"] is True
    assert report["fixture"]["profile"] == profile
    assert report["parity"]["equivalent"]["datum"]["content_match"] is True
    assert report["parity"]["equivalent"]["python"]["content_match"] is True
    assert report["determinism"]["equivalent"]["datum"]["output_stable"] is True
    assert report["source"]["commit"]


def test_workloads_reference_output_is_stable(tmp_path):
    fixture = tmp_path / "fixture"
    generate("csv-transform", fixture, target_bytes=100_000, seed=3)
    first = run_workload("python", "csv-transform", fixture / "input", tmp_path / "first.jsonl")
    second = run_workload("python", "csv-transform", fixture / "input", tmp_path / "second.jsonl")
    assert first["records"] == second["records"]
    assert first["output_sha256"] == second["output_sha256"]
    assert first["output_bytes"] > 0


def test_validator_rejects_tampered_summary(tmp_path):
    fixture = tmp_path / "fixture"
    generate("csv-transform", fixture, target_bytes=100_000, seed=4)
    report = {
        "run_schema_version": 1,
        "source": {"commit": "abc"},
        "environment": {"python_version": "3"},
        "fixture": {
            "profile": "csv-transform",
            "fixture_sha256": "deadbeef",
            "path": str(fixture),
        },
        "trials": [
            {
                "mode": "equivalent",
                "tool": "datum",
                "repetition": 1,
                "status": "completed",
                "wall_seconds": 1.0,
                "records": 1,
                "records_hash": "x",
            }
        ],
        "summary": {"equivalent": {"datum": {"wall_seconds_median": 999.0}}},
        "parity": {},
        "failures": [],
    }
    result = validate_report(report, fixtures_root=tmp_path)
    assert result["ok"] is False
    assert any(item["name"] == "summary recomputes" and not item["ok"] for item in result["checks"])
