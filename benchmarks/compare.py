"""Cross-tool comparison benchmark for DATUM.

The benchmark has two deliberately narrow workloads:

1. text-ingest: DATUM against a small independent Python reference implementation.
2. csv-transform: DATUM against pandas and DuckDB when those optional tools are installed.

DATUM performs a complete build (schema validation, manifest, quality report, and
post-build verification). The other tools only perform the stated data transform. The
report keeps that difference explicit and must not be presented as an apples-to-apples
product benchmark.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from datum import __version__  # noqa: E402
from datum.manifest import environment_info  # noqa: E402
from datum.util import canonical_json, sha256_file, utc_now_iso  # noqa: E402

WORDS = (
    "dataset record source schema quality manifest provenance chunk token "
    "compile verify deterministic local offline input output evidence contract "
    "alpha beta gamma delta epsilon zeta theta lambda sigma omega"
).split()


def _peak_rss_bytes() -> int | None:
    try:
        with open("/proc/self/status", "r", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    try:
        import resource

        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value) * (1024 if sys.platform != "darwin" else 1)
    except (ImportError, OSError, ValueError):
        return None


def _write_text_corpus(root: Path, files: int, file_kib: int) -> int:
    root.mkdir(parents=True, exist_ok=True)
    randomizer = random.Random(42)
    target = max(1, file_kib) * 1024
    previous: str | None = None
    total = 0
    for index in range(files):
        if index == 0:
            text = ""
        elif index == 1:
            text = "tiny"
        elif index > 0 and index % 16 == 15 and previous is not None:
            text = previous
        else:
            parts: list[str] = []
            length = 0
            while length < target:
                sentence = " ".join(randomizer.choice(WORDS) for _ in range(randomizer.randint(20, 50)))
                line = f"Document {index:04d}. {sentence.capitalize()}."
                parts.append(line)
                length += len(line) + 1
            text = "\n".join(parts) + "\n"
            previous = text
        path = root / f"document-{index:04d}.txt"
        path.write_text(text, encoding="utf-8", newline="\n")
        total += len(text.encode("utf-8"))
    return total


def _write_csv_corpus(path: Path, rows: int) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    previous: tuple[str, str] | None = None
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["id", "prompt", "completion", "category"])
        for index in range(rows):
            if index % 500 == 499 and previous is not None:
                prompt, completion = previous
            elif index % 1000 == 999:
                prompt, completion = "", ""
            elif index % 997 == 0:
                prompt, completion = f"  Prompt {index} with whitespace  ", ""
            else:
                prompt = f"  Prompt {index}: explain datum  "
                completion = f"Completion {index} keeps evidence and café ☕.  "
                if index % 7 == 0:
                    completion = completion.replace("café", "cafe")
            writer.writerow([index, prompt, completion, "benchmark"])
            if prompt.strip() or completion.strip():
                previous = (prompt, completion)
    return path.stat().st_size


def _text_config(workers: int) -> str:
    return f'''[datum]
config_version = 1

[source]
path = "text-input"

[normalize]
line_endings = "lf"
strip_outer_whitespace = true
strip_control_chars = true

[chunking]
enabled = false

[length]
min_chars = 1
max_chars = 1000000
oversize = "reject"

[dedup]
enabled = true
index = "memory"

[split]
enabled = false

[provenance]
mode = "off"

[output]
dir = "text-output"

[quality]
min_exported_records = 1

[cache]
enabled = false

[runtime]
workers = {workers}
progress = false
'''


def _csv_config(workers: int) -> str:
    return f'''[datum]
config_version = 1

[source]
path = "csv-input"
csv_has_header = true

[schema]
name = "completion"

[normalize]
line_endings = "lf"
strip_outer_whitespace = true
strip_control_chars = true

[chunking]
enabled = false

[length]
min_chars = 1
max_chars = 1000000
oversize = "reject"

[dedup]
enabled = true
index = "memory"

[split]
enabled = false

[provenance]
mode = "off"

[output]
dir = "csv-output"

[quality]
min_exported_records = 1

[cache]
enabled = false

[runtime]
workers = {workers}
progress = false
'''


def _reference_normalize(text: str) -> str:
    result = text.replace("\r\n", "\n").replace("\r", "\n")
    result = "".join(
        ch for ch in result if ch in "\n\t" or unicodedata.category(ch) != "Cc"
    )
    return result.strip()


def worker_reference_text(input_dir: Path, output_path: Path) -> dict[str, Any]:
    started = time.perf_counter()
    seen: set[str] = set()
    records = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for path in sorted(input_dir.rglob("*.txt"), key=lambda item: item.relative_to(input_dir).as_posix()):
            text = _reference_normalize(path.read_text(encoding="utf-8"))
            if not text:
                continue
            if text in seen:
                continue
            seen.add(text)
            handle.write(canonical_json({"text": text}) + "\n")
            records += 1
    duration = time.perf_counter() - started
    digest, size = sha256_file(output_path)
    return {
        "records": records,
        "output_bytes": size,
        "output_sha256": digest,
        "work_seconds": round(duration, 6),
        "peak_rss_bytes": _peak_rss_bytes(),
    }


def worker_pandas(input_path: Path, output_path: Path) -> dict[str, Any]:
    import pandas as pd

    started = time.perf_counter()
    frame = pd.read_csv(input_path, dtype=str, keep_default_na=False)
    frame = frame[["prompt", "completion"]].copy()
    for column in ("prompt", "completion"):
        frame[column] = frame[column].str.replace("\r\n", "\n", regex=False)
        frame[column] = frame[column].str.replace("\r", "\n", regex=False)
        frame[column] = frame[column].str.strip()
    frame = frame[(frame["prompt"].str.len() + frame["completion"].str.len()) > 0]
    frame = frame.drop_duplicates(subset=["prompt", "completion"], keep="first")
    records = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for prompt, completion in frame.itertuples(index=False, name=None):
            handle.write(
                json.dumps(
                    {"prompt": prompt, "completion": completion},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
            records += 1
    duration = time.perf_counter() - started
    digest, size = sha256_file(output_path)
    return {
        "records": records,
        "output_bytes": size,
        "output_sha256": digest,
        "work_seconds": round(duration, 6),
        "peak_rss_bytes": _peak_rss_bytes(),
        "pandas_version": pd.__version__,
    }


def worker_duckdb(input_path: Path, output_path: Path) -> dict[str, Any]:
    import duckdb

    started = time.perf_counter()
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute(
            "CREATE TABLE rows AS "
            "SELECT row_number() OVER () AS rid, prompt, completion "
            "FROM read_csv_auto(?, header=true, all_varchar=true)",
            [str(input_path)],
        )
        escaped = str(output_path).replace("'", "''")
        connection.execute(
            "COPY ("
            "SELECT prompt, completion FROM ("
            "SELECT trim(prompt) AS prompt, trim(completion) AS completion, "
            "row_number() OVER (PARTITION BY trim(prompt), trim(completion) ORDER BY rid) AS rn "
            "FROM rows"
            ") WHERE length(prompt) + length(completion) > 0 AND rn = 1"
            f") TO '{escaped}' (FORMAT JSON, ARRAY false)"
        )
    finally:
        connection.close()
    duration = time.perf_counter() - started
    digest, size = sha256_file(output_path)
    records = 0
    with output_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records += 1
    return {
        "records": records,
        "output_bytes": size,
        "output_sha256": digest,
        "work_seconds": round(duration, 6),
        "peak_rss_bytes": _peak_rss_bytes(),
        "duckdb_version": duckdb.__version__,
    }


def _run_worker(tool: str, input_path: Path, output_path: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        tool,
        "--input",
        str(input_path),
        "--output",
        str(output_path),
    ]
    started = time.perf_counter()
    completed = subprocess.run(command, capture_output=True, text=True, timeout=1800, check=False)
    wall = time.perf_counter() - started
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"{tool} worker failed with exit code {completed.returncode}: {detail}")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{tool} worker returned invalid JSON: {error}") from error
    result["wall_seconds"] = round(wall, 6)
    return result


def _run_datum(config_path: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "datum.cli",
        "build",
        "--config",
        str(config_path),
        "--no-cache",
        "--no-progress",
        "--json",
    ]
    environment = dict(os.environ)
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(ROOT) + (os.pathsep + existing if existing else "")
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
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"DATUM build failed with exit code {completed.returncode}: {detail}")
    payload = json.loads(completed.stdout)
    output_path = Path(payload["output_directory"]) / "dataset.jsonl"
    digest, size = sha256_file(output_path)
    return {
        "tool": "datum",
        "kind": "full build",
        "status": payload.get("status"),
        "wall_seconds": round(wall, 6),
        "build_seconds": payload.get("performance", {}).get("total_seconds"),
        "verification_seconds": payload.get("verification_seconds"),
        "records": payload.get("records", {}).get("exported"),
        "output_bytes": size,
        "output_sha256": digest,
        "peak_rss_bytes": payload.get("performance", {}).get("peak_rss_bytes"),
        "workers": payload.get("performance", {}).get("workers"),
        "output_path": str(output_path),
    }


def _read_records(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


def _output_content_match(paths: Sequence[Path]) -> bool:
    try:
        datasets = [_read_records(path) for path in paths]
    except (OSError, json.JSONDecodeError):
        return False
    canonical = [
        sorted(canonical_json(record) for record in records)
        for records in datasets
    ]
    return all(dataset == canonical[0] for dataset in canonical[1:])


def _normalize_tool_result(tool: str, result: dict[str, Any]) -> dict[str, Any]:
    return {"tool": tool, **result}


def run_comparison(
    *,
    rows: int = 30000,
    text_files: int = 48,
    text_kib: int = 16,
    workers: int = 2,
    tools: Sequence[str] = ("pandas", "duckdb"),
    workdir: Path | None = None,
    keep: bool = False,
) -> dict[str, Any]:
    if rows < 1 or text_files < 1 or text_kib < 1:
        raise ValueError("rows, text_files, and text_kib must be positive")
    temporary = workdir is None
    base = Path(tempfile.mkdtemp(prefix="datum-compare-")) if temporary else workdir.expanduser().resolve()
    if not temporary:
        base.mkdir(parents=True, exist_ok=True)
    text_input = base / "text-input"
    csv_input = base / "csv-input"
    if text_input.exists():
        shutil.rmtree(text_input)
    if csv_input.exists():
        shutil.rmtree(csv_input)
    text_input.mkdir(parents=True)
    csv_input.mkdir(parents=True)
    text_bytes = _write_text_corpus(text_input, text_files, text_kib)
    csv_path = csv_input / "pairs.csv"
    csv_bytes = _write_csv_corpus(csv_path, rows)

    text_config = base / "text.toml"
    csv_config = base / "csv.toml"
    text_config.write_text(_text_config(workers), encoding="utf-8", newline="\n")
    csv_config.write_text(_csv_config(workers), encoding="utf-8", newline="\n")

    text_tools: list[dict[str, Any]] = []
    csv_tools: list[dict[str, Any]] = []
    text_output_paths: list[Path] = []
    csv_output_paths: list[Path] = []

    datum_text = _run_datum(text_config)
    text_output_paths.append(Path(datum_text.pop("output_path")))
    text_tools.append(_normalize_tool_result("datum", datum_text))

    reference_output = base / "reference-text.jsonl"
    reference = _run_worker("reference-text", text_input, reference_output)
    text_output_paths.append(reference_output)
    text_tools.append(_normalize_tool_result("python-reference", reference))

    datum_csv = _run_datum(csv_config)
    csv_output_paths.append(Path(datum_csv.pop("output_path")))
    csv_tools.append(_normalize_tool_result("datum", datum_csv))

    skipped: list[dict[str, str]] = []
    for tool, worker, output_name in (
        ("pandas", worker_pandas, "pandas.jsonl"),
        ("duckdb", worker_duckdb, "duckdb.jsonl"),
    ):
        if tool not in tools:
            skipped.append({"workload": "csv-transform", "tool": tool, "reason": "not requested"})
            continue
        if importlib.util.find_spec(tool) is None:
            skipped.append({"workload": "csv-transform", "tool": tool, "reason": "not installed"})
            continue
        output = base / output_name
        result = _run_worker(tool, csv_path, output)
        csv_output_paths.append(output)
        csv_tools.append(_normalize_tool_result(tool, result))

    report = {
        "comparison_schema_version": 1,
        "created_at": utc_now_iso(),
        "datum_version": __version__,
        "mode": "cross-tool-comparison",
        "environment": environment_info(),
        "fixture": {
            "text": {
                "files": text_files,
                "file_kib": text_kib,
                "input_bytes": text_bytes,
            },
            "csv": {
                "rows": rows,
                "input_bytes": csv_bytes,
            },
            "workers": workers,
        },
        "workloads": {
            "text-ingest": {
                "description": "Read sorted text files, normalize, drop empty/duplicate text, write JSONL.",
                "tools": text_tools,
                "output_content_match": _output_content_match(text_output_paths),
                "datum_extra_work": "Schema validation, quality accounting, manifest, and post-build verification.",
            },
            "csv-transform": {
                "description": "Read CSV prompt/completion pairs, trim fields, drop empty/duplicate pairs, write JSONL.",
                "tools": csv_tools,
                "output_content_match": _output_content_match(csv_output_paths),
                "datum_extra_work": "Schema validation, quality accounting, manifest, and post-build verification.",
            },
        },
        "skipped": skipped,
        "notes": [
            "DATUM runs a complete build; pandas, DuckDB, and the Python reference run only the stated transform.",
            "The Python reference is an internal baseline, not a marketed data product.",
            "Wall time includes Python process startup for all tools.",
            "These synthetic fixtures measure mechanics and overhead, not data quality or downstream model performance.",
            "Do not present this report as a general performance ranking.",
        ],
    }
    if temporary and not keep:
        shutil.rmtree(base, ignore_errors=True)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare DATUM with optional data-processing baselines.")
    parser.add_argument("--rows", type=int, default=30000)
    parser.add_argument("--text-files", type=int, default=48)
    parser.add_argument("--text-kib", type=int, default=16)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--tools", default="pandas,duckdb", help="comma-separated optional tools")
    parser.add_argument("--workdir", help="retain fixtures and outputs here")
    parser.add_argument("--json-out", help="write the report to a file")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--worker", choices=["reference-text", "pandas", "duckdb"], help=argparse.SUPPRESS)
    parser.add_argument("--input", help=argparse.SUPPRESS)
    parser.add_argument("--output", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        if not args.input or not args.output:
            parser.error("--input and --output are required for worker mode")
        input_path = Path(args.input)
        output_path = Path(args.output)
        if args.worker == "reference-text":
            result = worker_reference_text(input_path, output_path)
        elif args.worker == "pandas":
            result = worker_pandas(input_path, output_path)
        else:
            result = worker_duckdb(input_path, output_path)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    try:
        report = run_comparison(
            rows=args.rows,
            text_files=args.text_files,
            text_kib=args.text_kib,
            workers=args.workers,
            tools=tuple(item.strip() for item in args.tools.split(",") if item.strip()),
            workdir=Path(args.workdir) if args.workdir else None,
            keep=bool(args.workdir),
        )
    except (RuntimeError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if args.json_out:
        target = Path(args.json_out).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(f"DATUM {report['datum_version']} cross-tool comparison")
        for workload, payload in report["workloads"].items():
            print(f"  {workload}: content match={payload['output_content_match']}")
            for tool in payload["tools"]:
                print(
                    f"    {tool['tool']:<17} wall={tool['wall_seconds']:.3f}s "
                    f"records={tool['records']} peak={tool.get('peak_rss_bytes')}"
                )
        if report["skipped"]:
            print("  skipped:", ", ".join(f"{item['tool']} ({item['reason']})" for item in report["skipped"]))
        print("Cross-tool mechanics comparison; not a general performance ranking.")
    return 0 if all(item["output_content_match"] for item in report["workloads"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
