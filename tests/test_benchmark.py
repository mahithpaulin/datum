from __future__ import annotations

from datum.benchmark import run_benchmark


def test_synthetic_benchmark_smoke(tmp_path):
    report = run_benchmark(files=2, file_kib=1, workers=1, workdir=tmp_path / "bench", keep=True)
    assert report["benchmark_schema_version"] == 1
    assert report["fixture"]["files"] == 2
    assert len(report["runs"]) == 3
    labels = [run["label"] for run in report["runs"]]
    assert labels == ["cold-no-cache", "cold-cache", "warm-cache"]
    assert report["deterministic_outputs"] is True
    warm = report["runs"][2]
    assert warm["cache"]["hits"] == warm["files_processed"]
    assert (tmp_path / "bench" / "output" / "current" / "manifest.json").is_file()
