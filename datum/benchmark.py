"""Reproducible synthetic benchmark runner for DATUM builds."""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .errors import BuildError, ConfigError
from .manifest import environment_info, summarize_manifest
from .util import atomic_write_json, human_bytes, human_duration, utc_now_iso

WORDS = (
    "dataset record source schema quality manifest provenance chunk token "
    "compile verify deterministic local offline input output evidence contract "
    "alpha beta gamma delta epsilon zeta theta lambda sigma omega"
).split()


def _write_corpus(root: Path, files: int, file_kib: int, seed: int) -> tuple[int, int]:
    randomizer = random.Random(seed)
    total = 0
    target = max(1, file_kib) * 1024
    for index in range(files):
        parts: list[str] = []
        length = 0
        topic = randomizer.choice(WORDS)
        while length < target:
            paragraph = " ".join(
                randomizer.choice(WORDS) for _ in range(randomizer.randint(24, 60))
            )
            sentence = f"Document {index:05d} discusses {topic}. {paragraph.capitalize()}."
            parts.append(sentence)
            parts.append("")
            length += len(sentence) + 1
        text = "\n".join(parts) + "\n"
        path = root / f"document-{index:05d}.txt"
        path.write_text(text, encoding="utf-8", newline="\n")
        total += len(text.encode("utf-8"))
    return files, total


def _benchmark_config(workers: int) -> str:
    return f'''[datum]
config_version = 1

[source]
path = "input"

[normalize]
line_endings = "lf"
strip_outer_whitespace = true

[chunking]
enabled = true
unit = "chars"
size = 8000
overlap = 400

[length]
min_chars = 20
max_chars = 500000
oversize = "reject"

[dedup]
enabled = true
index = "memory"

[split]
enabled = false

[provenance]
mode = "sidecar"

[output]
dir = "output"

[quality]
min_exported_records = 1
max_file_error_rate = 0.0
max_schema_rejection_rate = 0.0
max_duplicate_rate = 0.0

[cache]
enabled = true

[runtime]
workers = {workers}
progress = false
'''


def _run_build(config_path: Path, *, cache: bool, workers: int) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "datum.cli",
        "build",
        "--config",
        str(config_path),
        "--no-progress",
        "--json",
    ]
    if not cache:
        command.append("--no-cache")
    if workers > 0:
        command.extend(["--workers", str(workers)])
    environment = dict(os.environ)
    package_root = str(Path(__file__).resolve().parent.parent)
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = package_root + (os.pathsep + existing if existing else "")
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=environment,
        timeout=1800,
        check=False,
    )
    wall = time.perf_counter() - started
    if completed.returncode not in (0, 2):
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise BuildError(f"benchmark build failed with exit code {completed.returncode}: {detail}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise BuildError(f"benchmark build returned invalid JSON: {error}") from error
    payload["wall_seconds"] = round(wall, 6)
    return payload


def _manifest_identity(output_directory: str) -> tuple[str | None, str | None]:
    path = Path(output_directory) / "manifest.json"
    if not path.is_file():
        return None, None
    try:
        with path.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None, None
    return manifest.get("build", {}).get("output_identity"), manifest.get("build", {}).get("build_id")


def _run_summary(label: str, payload: dict[str, Any], identity: str | None) -> dict[str, Any]:
    performance = payload.get("performance") or {}
    files = payload.get("files") or {}
    records = payload.get("records") or {}
    cache = payload.get("cache") or {}
    wall = float(payload.get("wall_seconds") or performance.get("total_seconds") or 0.0)
    input_bytes = int(files.get("input_bytes_processed") or performance.get("input_bytes") or 0)
    output_bytes = int(performance.get("output_bytes") or 0)
    exported = int(records.get("exported") or 0)
    return {
        "label": label,
        "status": payload.get("status"),
        "build_id": payload.get("build_id"),
        "output_identity": identity,
        "wall_seconds": round(wall, 6),
        "build_seconds": performance.get("total_seconds"),
        "processing_seconds": performance.get("processing_seconds"),
        "compile_seconds": performance.get("compile_seconds"),
        "verification_seconds": payload.get("verification_seconds"),
        "files_processed": files.get("processed", 0),
        "source_records": records.get("source_records_extracted", 0),
        "exported_records": exported,
        "input_bytes": input_bytes,
        "output_bytes": output_bytes,
        "files_per_second": round(files.get("processed", 0) / wall, 3) if wall > 0 else None,
        "exported_records_per_second": round(exported / wall, 3) if wall > 0 else None,
        "input_mib_per_second": round(input_bytes / (1024 * 1024) / wall, 3) if wall > 0 else None,
        "peak_rss_bytes": performance.get("peak_rss_bytes"),
        "workers": performance.get("workers"),
        "cache": {
            "hits": cache.get("hits", 0),
            "misses": cache.get("misses", 0),
            "writes": cache.get("writes", 0),
            "invalid": cache.get("invalid", 0),
        },
    }


def run_benchmark(
    *,
    files: int = 32,
    file_kib: int = 64,
    workers: int = 0,
    workdir: Path | None = None,
    keep: bool = False,
) -> dict[str, Any]:
    if files < 1:
        raise ConfigError("benchmark files must be at least 1")
    if file_kib < 1:
        raise ConfigError("benchmark file-kib must be at least 1")
    if workers < 0:
        raise ConfigError("benchmark workers must be zero or positive")
    temporary = workdir is None
    if workdir is None:
        base = Path(tempfile.mkdtemp(prefix="datum-benchmark-"))
    else:
        base = workdir.expanduser().resolve()
        base.mkdir(parents=True, exist_ok=True)
    input_dir = base / "input"
    if input_dir.exists():
        shutil.rmtree(input_dir)
    input_dir.mkdir(parents=True)
    file_count, input_bytes = _write_corpus(input_dir, files, file_kib, 42)
    config_path = base / "benchmark.toml"
    config_path.write_text(_benchmark_config(workers), encoding="utf-8", newline="\n")
    runs: list[dict[str, Any]] = []
    try:
        cold = _run_build(config_path, cache=False, workers=workers)
        identity_cold, build_id_cold = _manifest_identity(cold.get("output_directory", ""))
        runs.append(_run_summary("cold-no-cache", cold, identity_cold))

        cache_cold = _run_build(config_path, cache=True, workers=workers)
        identity_cached, build_id_cached = _manifest_identity(cache_cold.get("output_directory", ""))
        runs.append(_run_summary("cold-cache", cache_cold, identity_cached))

        warm = _run_build(config_path, cache=True, workers=workers)
        identity_warm, build_id_warm = _manifest_identity(warm.get("output_directory", ""))
        runs.append(_run_summary("warm-cache", warm, identity_warm))

        deterministic = (
            identity_cold is not None
            and identity_cold == identity_cached
            and identity_cached == identity_warm
            and build_id_cold == build_id_cached == build_id_warm
        )
        report = {
            "benchmark_schema_version": 1,
            "datum_version": __version__,
            "created_at": utc_now_iso(),
            "mode": "synthetic-text",
            "environment": environment_info(),
            "fixture": {
                "files": file_count,
                "file_kib": file_kib,
                "input_bytes": input_bytes,
                "chunking": {"unit": "chars", "size": 8000, "overlap": 400},
                "workers": workers,
            },
            "runs": runs,
            "deterministic_outputs": deterministic,
            "notes": [
                "Wall time includes Python process startup; build_seconds is measured inside the build.",
                "input_mib_per_second uses wall time and processed source bytes.",
                "This synthetic corpus measures pipeline mechanics, not model quality or real-world corpus diversity.",
                "Do not present these numbers as a claim about all hardware or all datasets.",
            ],
        }
        if temporary and not keep:
            pass
        return report
    finally:
        if temporary and not keep:
            shutil.rmtree(base, ignore_errors=True)


def benchmark_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="datum benchmark", description="Run a reproducible synthetic build benchmark.")
    parser.add_argument("--files", type=int, default=32, help="number of generated documents")
    parser.add_argument("--file-kib", type=int, default=64, help="approximate KiB per document")
    parser.add_argument("--workers", type=int, default=0, help="worker threads; zero means automatic")
    parser.add_argument("--workdir", help="retain benchmark inputs and outputs in this directory")
    parser.add_argument("--json-out", help="write the benchmark report to a file")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    try:
        report = run_benchmark(
            files=args.files,
            file_kib=args.file_kib,
            workers=args.workers,
            workdir=Path(args.workdir) if args.workdir else None,
            keep=bool(args.workdir),
        )
    except (BuildError, ConfigError) as error:
        print(f"error: {error}", file=sys.stderr)
        return error.exit_code
    if args.json_out:
        atomic_write_json(Path(args.json_out).expanduser(), report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        fixture = report["fixture"]
        print(f"DATUM {report['datum_version']} synthetic benchmark")
        print(f"Fixture: {fixture['files']} files, {human_bytes(fixture['input_bytes'])} input")
        for run in report["runs"]:
            print(
                f"  {run['label']:<14} wall={human_duration(run['wall_seconds'])} "
                f"build={human_duration(float(run['build_seconds'] or 0))} "
                f"files/s={run['files_per_second']} records/s={run['exported_records_per_second']} "
                f"cache={run['cache']['hits']}h/{run['cache']['misses']}m"
            )
        print(f"Deterministic outputs: {'yes' if report['deterministic_outputs'] else 'no'}")
        print("Synthetic mechanics benchmark; not a general performance claim.")
    return 0 if report["deterministic_outputs"] else 1


if __name__ == "__main__":
    raise SystemExit(benchmark_main())
