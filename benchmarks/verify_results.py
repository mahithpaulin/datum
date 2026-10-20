"""Independent validation of benchmark reports and trial evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence


def canonical_record(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"record in {path} is not a JSON object")
            records.append(value)
    return records


def record_keys(records: Iterable[dict[str, Any]]) -> list[str]:
    return [canonical_record(record) for record in records]


def compare_records(
    expected: Sequence[dict[str, Any]],
    actual: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    expected_keys = record_keys(expected)
    actual_keys = record_keys(actual)
    expected_set = set(expected_keys)
    actual_set = set(actual_keys)
    missing = expected_set - actual_set
    extra = actual_set - expected_set
    duplicates = len(actual_keys) - len(actual_set)
    return {
        "expected_records": len(expected_keys),
        "actual_records": len(actual_keys),
        "missing": len(missing),
        "extra": len(extra),
        "duplicates": duplicates,
        "content_match": not missing and not extra and duplicates == 0,
        "missing_samples": sorted(missing)[:3],
        "extra_samples": sorted(extra)[:3],
    }


def dataset_paths(output_dir: str | Path) -> list[Path]:
    current = Path(output_dir)
    if current.name == "current":
        current = current
    else:
        candidate = current / "current"
        if candidate.is_dir():
            current = candidate
    dataset = current / "dataset.jsonl"
    if dataset.is_file():
        return [dataset]
    paths: list[Path] = []
    for name in ("train.jsonl", "validation.jsonl", "test.jsonl"):
        path = current / name
        if path.is_file():
            paths.append(path)
    return paths


def output_records(output_dir: str | Path) -> list[dict[str, Any]]:
    paths = dataset_paths(output_dir)
    if not paths:
        raise FileNotFoundError(f"no dataset output found in {output_dir}")
    records: list[dict[str, Any]] = []
    for path in paths:
        records.extend(load_jsonl(path))
    return records


def output_files(output_dir: str | Path, *, include_metadata: bool = False) -> list[Path]:
    current = Path(output_dir)
    if not (current / "current").is_dir() and current.name != "current":
        current = current
    else:
        current = current / "current" if (current / "current").is_dir() else current
    excluded = {"config.resolved.json", "manifest.json", "quality.json", "quality.txt", "rejected.jsonl"}
    if not include_metadata:
        excluded.update({"provenance.jsonl"})
    paths = [
        path
        for path in current.rglob("*")
        if path.is_file() and path.name not in excluded and "sidecar" not in path.parts
    ]
    return sorted(paths, key=lambda item: item.relative_to(current).as_posix())


def output_sha256(output_dir: str | Path, *, include_metadata: bool = False) -> str:
    digest = hashlib.sha256()
    paths = output_files(output_dir, include_metadata=include_metadata)
    base = Path(output_dir)
    if (base / "current").is_dir():
        base = base / "current"
    for path in paths:
        digest.update(path.relative_to(base).as_posix().encode("utf-8"))
        digest.update(b"\0")
        file_digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                file_digest.update(chunk)
        digest.update(file_digest.digest())
    return digest.hexdigest()


def _quartiles(values: Sequence[float]) -> tuple[float, float]:
    if len(values) == 1:
        return values[0], values[0]
    quantiles = statistics.quantiles(values, n=4, method="inclusive")
    return quantiles[0], quantiles[2]


def summarize_trials(trials: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for trial in trials:
        mode = trial.get("mode", "equivalent")
        tool = trial.get("tool", "unknown")
        grouped.setdefault(mode, {}).setdefault(tool, []).append(trial)
    summary: dict[str, dict[str, Any]] = {}
    for mode, tools in grouped.items():
        summary[mode] = {}
        for tool, runs in tools.items():
            completed = [run for run in runs if run.get("status") == "completed"]
            walls = [float(run["wall_seconds"]) for run in completed if run.get("wall_seconds") is not None]
            entry: dict[str, Any] = {
                "attempts": len(runs),
                "completed": len(completed),
                "failed": len(runs) - len(completed),
                "tool_versions": {
                    key: value
                    for run in completed
                    for key, value in (run.get("versions") or {}).items()
                },
            }
            if walls:
                ordered = sorted(walls)
                p25, p75 = _quartiles(ordered)
                entry.update(
                    {
                        "wall_seconds_median": round(statistics.median(ordered), 6),
                        "wall_seconds_min": round(min(ordered), 6),
                        "wall_seconds_max": round(max(ordered), 6),
                        "wall_seconds_iqr": round(p75 - p25, 6),
                        "wall_seconds_runs": [round(value, 6) for value in walls],
                    }
                )
            peaks = [int(run["peak_rss_bytes"]) for run in completed if run.get("peak_rss_bytes")]
            if peaks:
                entry["peak_rss_bytes_max"] = max(peaks)
            records = [run.get("records") for run in completed if run.get("records") is not None]
            if records:
                entry["records"] = records[0]
                entry["record_counts_stable"] = len(set(records)) == 1
            hashes = [run.get("output_sha256") for run in completed if run.get("output_sha256")]
            if hashes:
                entry["output_sha256"] = hashes[0]
                entry["output_stable"] = len(set(hashes)) == 1
            summary[mode][tool] = entry
    return summary


def validate_report(report: dict[str, Any], *, fixtures_root: str | Path | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    check("report schema", report.get("run_schema_version") == 1, report.get("run_schema_version"))
    source = report.get("source") or {}
    check("source commit recorded", bool(source.get("commit")), source.get("commit"))
    environment = report.get("environment") or {}
    check("environment recorded", bool(environment.get("python_version")), environment.get("python_version"))
    fixture = report.get("fixture") or {}
    check(
        "fixture manifest recorded",
        bool(fixture.get("profile")) and fixture.get("input_bytes") is not None,
        fixture.get("profile"),
    )
    trials = report.get("trials") or []
    check("trials present", len(trials) > 0, len(trials))
    completed = [trial for trial in trials if trial.get("status") == "completed"]
    check("completed trials present", len(completed) > 0, len(completed))
    bad_walls = [trial for trial in completed if trial.get("wall_seconds") is None]
    check("completed trials have wall time", not bad_walls, len(bad_walls))
    datum_failures = [
        trial
        for trial in trials
        if trial.get("tool") == "datum" and trial.get("status") != "completed"
    ]
    check("DATUM trials preserved", not datum_failures or bool(report.get("failures")), len(datum_failures))
    recomputed = summarize_trials(trials)
    check("summary recomputes", recomputed == report.get("summary"), "summary differs")
    parity = report.get("parity") or {}
    if "equivalent" in recomputed:
        equivalent_tools = recomputed.get("equivalent", {})
        check("equivalent has DATUM", "datum" in equivalent_tools, sorted(equivalent_tools))
        if "python" in equivalent_tools and "datum" in equivalent_tools:
            check(
                "Python reference completed",
                equivalent_tools["python"].get("completed", 0) > 0,
                equivalent_tools["python"].get("failed"),
            )
    for mode, tools in recomputed.items():
        for tool, entry in tools.items():
            check(
                f"deterministic {mode}/{tool}",
                entry.get("output_stable") is True,
                entry.get("record_counts"),
            )
    for mode, results in parity.items():
        for tool, result in results.items():
            if not isinstance(result, dict):
                continue
            check(
                f"parity {mode}/{tool}",
                bool(result.get("content_match")),
                {key: result.get(key) for key in ("missing", "extra", "duplicates")},
            )
    if fixtures_root is not None and fixture.get("path"):
        expected_hash = fixture.get("fixture_sha256")
        actual = fixture_path(fixtures_root, fixture)
        check("fixture path exists", actual.is_dir(), str(actual))
        if actual.is_dir() and expected_hash:
            input_dir = actual / "input"
            check("fixture hash", tree_sha256(input_dir) == expected_hash, expected_hash)
    ok = all(item["ok"] for item in checks)
    return {"ok": ok, "checks": checks}


def fixture_path(fixtures_root: str | Path, fixture: dict[str, Any]) -> Path:
    root = Path(fixtures_root)
    name = fixture.get("path")
    if name:
        candidate = Path(name)
        return candidate if candidate.is_absolute() else root / candidate
    profile = fixture.get("profile", "fixture")
    tier = (fixture.get("plan") or {}).get("tier", "A")
    return root / f"{profile}-{tier}"


def tree_sha256(root: str | Path) -> str:
    base = Path(root)
    digest = hashlib.sha256()
    for path in sorted(base.rglob("*"), key=lambda item: item.relative_to(base).as_posix()):
        if not path.is_file():
            continue
        digest.update(path.relative_to(base).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(path.stat().st_size).encode("ascii"))
        digest.update(b"\0")
        file_digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                file_digest.update(chunk)
        digest.update(file_digest.digest())
    return digest.hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a DATUM benchmark report.")
    parser.add_argument("--report", required=True)
    parser.add_argument("--fixtures-root")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
        result = validate_report(report, fixtures_root=args.fixtures_root)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for item in result["checks"]:
            print(f"{'ok' if item['ok'] else 'FAIL'}  {item['name']}")
        print("report valid" if result["ok"] else "report invalid")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
