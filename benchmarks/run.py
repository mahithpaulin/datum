"""Configurable DATUM competitive benchmark controller.

This script is deliberately separate from the small CI smoke benchmark in
``datum.benchmark``. It generates a fixture, records every trial, supports resume,
and keeps failures rather than silently dropping them.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import resource
import shutil
import statistics
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

BENCHMARKS = Path(__file__).resolve().parent
ROOT = BENCHMARKS.parent
if str(BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from generate_data import PROFILES, TIER_BYTES, generate  # noqa: E402
from verify_results import (  # noqa: E402
    canonical_record,
    load_jsonl,
    output_records,
    output_sha256,
    summarize_trials,
    validate_report,
)

TOOL_ORDER = ("datum", "python", "pandas", "duckdb", "polars")
PROFILE_TOOLS = {
    "many-small-text": {"equivalent": ("datum", "python", "pandas", "duckdb", "polars")},
    "large-text": {"equivalent": ("datum",)},
    "jsonl-training": {"equivalent": ("datum", "python", "pandas", "duckdb", "polars")},
    "csv-transform": {"equivalent": ("datum", "python", "pandas", "duckdb", "polars")},
    "mixed-formats": {"equivalent": ("datum",)},
    "adversarial-quality": {"equivalent": ("datum",)},
}
WORKFLOW_TOOLS = ("datum",)


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _module_version(name: str) -> str | None:
    spec = importlib.util.find_spec(name)
    if spec is None:
        return None
    try:
        module = __import__(name)
    except Exception:  # noqa: BLE001 - availability probes must never fail the run
        return None
    return str(getattr(module, "__version__", "installed"))


def _available_tools() -> dict[str, bool]:
    return {
        "datum": True,
        "python": True,
        "pandas": importlib.util.find_spec("pandas") is not None,
        "duckdb": importlib.util.find_spec("duckdb") is not None,
        "polars": importlib.util.find_spec("polars") is not None,
    }


def _memory_bytes() -> int | None:
    try:
        with open("/proc/meminfo", "r", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    try:
        import psutil

        return int(psutil.virtual_memory().available)
    except Exception:  # noqa: BLE001
        return None


def _cpu_name() -> str | None:
    try:
        with open("/proc/cpuinfo", "r", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except (OSError, IndexError):
        return platform.processor() or None
    return platform.processor() or None


def _environment() -> dict[str, Any]:
    disk = shutil.disk_usage(ROOT)
    return {
        "python_version": sys.version,
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": _cpu_name(),
        "cpu_count": os.cpu_count(),
        "memory_available_bytes": _memory_bytes(),
        "disk_free_bytes": disk.free,
        "disk_total_bytes": disk.total,
        "tool_versions": {
            name: _module_version(name)
            for name in ("datum", "pandas", "duckdb", "polars", "pyarrow", "psutil", "datasets")
        },
        "environment_variables": {
            key: os.environ.get(key)
            for key in (
                "KAGGLE_KERNEL_RUN_TYPE",
                "KAGGLE_URL_BASE",
                "CUDA_VISIBLE_DEVICES",
                "OMP_NUM_THREADS",
                "MKL_NUM_THREADS",
                "PYTHONHASHSEED",
            )
        },
    }


def _source() -> dict[str, Any]:
    status = _git("status", "--porcelain")
    return {
        "commit": _git("rev-parse", "HEAD"),
        "branch": _git("branch", "--show-current"),
        "dirty": bool(status),
        "dirty_files": (status or "").splitlines()[:20],
        "remote": _git("remote", "get-url", "origin"),
        "describe": _git("describe", "--always", "--dirty"),
    }


def _resource_check(target_bytes: int, *, force: bool, minimum_disk_mib: int) -> dict[str, Any]:
    memory = _memory_bytes()
    disk = shutil.disk_usage(ROOT)
    needed_memory = int(target_bytes * 1.5) + 256 * 1024 * 1024
    needed_disk = int(target_bytes * 3.0) + minimum_disk_mib * 1024 * 1024
    memory_ok = memory is None or memory >= needed_memory
    disk_ok = disk.free >= needed_disk
    return {
        "target_bytes": target_bytes,
        "needed_memory_bytes": needed_memory,
        "available_memory_bytes": memory,
        "needed_disk_bytes": needed_disk,
        "available_disk_bytes": disk.free,
        "memory_ok": memory_ok,
        "disk_ok": disk_ok,
        "allowed": force or (memory_ok and disk_ok),
        "forced": force,
    }


def _startup_probe(tools: Sequence[str], timeout: float = 60) -> dict[str, float]:
    modules = {"datum": "datum", "python": "json", "pandas": "pandas", "duckdb": "duckdb", "polars": "polars"}
    result: dict[str, float] = {}
    for tool in tools:
        module = modules.get(tool)
        if not module:
            continue
        started = time.perf_counter()
        completed = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        result[tool] = round(time.perf_counter() - started, 6) if completed.returncode == 0 else -1.0
    return result


class _Monitor:
    def __init__(self) -> None:
        self.peak = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pid: int | None = None

    def start(self, pid: int) -> None:
        try:
            import psutil
        except Exception:  # noqa: BLE001
            return
        self._pid = pid

        def sample() -> None:
            process = psutil.Process(pid)
            while not self._stop.is_set():
                try:
                    total = process.memory_info().rss
                    for child in process.children(recursive=True):
                        try:
                            total += child.memory_info().rss
                        except Exception:  # noqa: BLE001
                            pass
                    self.peak = max(self.peak, total)
                except Exception:  # noqa: BLE001
                    break
                self._stop.wait(0.05)

        self._thread = threading.Thread(target=sample, daemon=True)
        self._thread.start()

    def stop(self) -> int:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        return self.peak


def _execute(
    command: Sequence[str],
    *,
    timeout: float,
    cwd: Path,
    env: dict[str, str] | None = None,
    stdout_path: Path,
    stderr_path: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    before_cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
    monitor = _Monitor()
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    status = "completed"
    timed_out = False
    with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr_handle:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=env,
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
            start_new_session=True,
        )
        monitor.start(process.pid)
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            status = "timeout"
            try:
                os.killpg(process.pid, 15)
            except OSError:
                process.kill()
            try:
                returncode = process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, 9)
                except OSError:
                    process.kill()
                returncode = process.wait(timeout=15)
        finally:
            wall = time.perf_counter() - started
            peak = monitor.stop()
    after_cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu_seconds = max(
        0.0,
        (after_cpu.ru_utime + after_cpu.ru_stime) - (before_cpu.ru_utime + before_cpu.ru_stime),
    )
    stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
    stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
    if returncode != 0 and status == "completed":
        status = "failed"
    oom = returncode in (-9, 137) or "MemoryError" in stderr_text or "out of memory" in stderr_text.lower()
    if oom:
        status = "oom"
    return {
        "status": status,
        "exit_code": returncode,
        "timed_out": timed_out,
        "oom": oom,
        "wall_seconds": round(wall, 6),
        "cpu_seconds": round(cpu_seconds, 6),
        "monitor_peak_rss_bytes": peak,
        "stdout_tail": stdout_text[-4000:],
        "stderr_tail": stderr_text[-4000:],
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }


def _parse_json(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _canonical_hash(records: Sequence[dict[str, Any]]) -> str:
    import hashlib

    digest = hashlib.sha256()
    for key in sorted(canonical_record(record) for record in records):
        digest.update(key.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _output_bytes(output_dir: Path) -> int:
    paths = list(output_dir.rglob("*.jsonl")) if output_dir.is_dir() else []
    return sum(path.stat().st_size for path in paths)


def _datum_output(output_dir: Path) -> Path:
    current = output_dir / "current"
    return current if current.is_dir() else output_dir


def _trial_key(trial: dict[str, Any]) -> tuple[str, str, int]:
    return (str(trial.get("mode")), str(trial.get("tool")), int(trial.get("repetition", 0)))


def _run_datum(
    fixture_root: Path,
    output_dir: Path,
    mode: str,
    timeout: float,
    *,
    verify: bool,
) -> dict[str, Any]:
    config = fixture_root / ("datum-workflow.toml" if mode == "workflow" else "datum.toml")
    if not config.is_file():
        raise FileNotFoundError(f"missing fixture config: {config}")
    output_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + existing if existing else "")
    command = [
        sys.executable,
        "-m",
        "datum.cli",
        "build",
        "--config",
        str(config),
        "--output",
        str(output_dir),
        "--no-cache",
        "--no-progress",
        "--json",
    ]
    execution = _execute(
        command,
        timeout=timeout,
        cwd=ROOT,
        env=env,
        stdout_path=output_dir / "benchmark.stdout.json",
        stderr_path=output_dir / "benchmark.stderr.txt",
    )
    stdout_text = Path(execution["stdout_path"]).read_text(encoding="utf-8", errors="replace")
    payload = _parse_json(stdout_text)
    result: dict[str, Any] = {
        "tool": "datum",
        "kind": "full build",
        "command": command,
        **execution,
        "versions": {"datum": _module_version("datum")},
    }
    if payload is not None:
        current = _datum_output(output_dir)
        try:
            records = output_records(current)
            result.update(
                {
                    "records": len(records),
                    "record_counts_stable": True,
                    "records_hash": _canonical_hash(records),
                    "output_sha256": output_sha256(current),
                    "output_bytes": _output_bytes(current),
                }
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            result["output_error"] = str(error)
        performance = payload.get("performance") or {}
        result.update(
            {
                "build_seconds": performance.get("total_seconds"),
                "internal_seconds": performance.get("total_seconds"),
                "processing_seconds": performance.get("processing_seconds"),
                "verification_seconds_internal": performance.get("verification_seconds"),
                "peak_rss_bytes": performance.get("peak_rss_bytes"),
                "workers": performance.get("workers"),
                "quality_status": payload.get("quality_status"),
                "build_status": payload.get("status"),
                "records_reported": (payload.get("records") or {}).get("exported"),
                "rejections": payload.get("records"),
                "splits": payload.get("splits"),
                "output_directory": payload.get("output_directory"),
                "exported_matches_output": result.get("records") == (payload.get("records") or {}).get("exported"),
            }
        )
        if result.get("records") != (payload.get("records") or {}).get("exported"):
            result["status"] = "failed"
            result["failure"] = "output record count does not match build report"
    elif execution["status"] == "completed":
        result["status"] = "failed"
        result["failure"] = "build did not emit a JSON object"
    current = _datum_output(output_dir)
    if verify and result.get("status") == "completed" and current.is_dir():
        verify_command = [
            sys.executable,
            "-m",
            "datum.cli",
            "verify",
            str(current),
            "--sources",
            "--json",
        ]
        verified = _execute(
            verify_command,
            timeout=timeout,
            cwd=ROOT,
            env=env,
            stdout_path=output_dir / "verify.stdout.json",
            stderr_path=output_dir / "verify.stderr.txt",
        )
        verify_text = Path(verified["stdout_path"]).read_text(encoding="utf-8", errors="replace")
        verify_payload = _parse_json(verify_text)
        result["verify"] = {
            "status": verified["status"],
            "wall_seconds": verified["wall_seconds"],
            "ok": bool(verify_payload and verify_payload.get("ok")),
            "checks": (verify_payload or {}).get("checks"),
            "stderr_tail": verified["stderr_tail"][-1000:],
        }
        if not result["verify"]["ok"]:
            result["status"] = "failed"
            result["failure"] = "independent verification failed"
    return result


def _run_competitor(
    tool: str,
    profile: str,
    fixture_root: Path,
    output_dir: Path,
    timeout: float,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{tool}.jsonl"
    command = [
        sys.executable,
        str(BENCHMARKS / "workloads.py"),
        "--tool",
        tool,
        "--profile",
        profile,
        "--input",
        str(fixture_root / "input"),
        "--output",
        str(output_path),
    ]
    execution = _execute(
        command,
        timeout=timeout,
        cwd=ROOT,
        stdout_path=output_dir / "workload.stdout.json",
        stderr_path=output_dir / "workload.stderr.txt",
    )
    stdout_text = Path(execution["stdout_path"]).read_text(encoding="utf-8", errors="replace")
    payload = _parse_json(stdout_text)
    result: dict[str, Any] = {
        "tool": tool,
        "kind": "independent transform",
        "command": command,
        **execution,
        "versions": (payload or {}).get("versions") or {},
    }
    if payload and payload.get("status") == "ok":
        result.update(
            {
                "records": payload.get("records"),
                "records_hash": payload.get("records_sha256"),
                "output_sha256": payload.get("output_sha256"),
                "output_bytes": payload.get("output_bytes"),
                "internal_seconds": payload.get("work_seconds"),
                "peak_rss_bytes": payload.get("peak_rss_bytes"),
                "output_path": str(output_path),
            }
        )
        try:
            records = load_jsonl(output_path)
            result["records"] = len(records)
            result["records_hash"] = _canonical_hash(records)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            result["status"] = "failed"
            result["failure"] = f"invalid output: {error}"
    elif execution["status"] == "completed":
        result["status"] = "failed"
        result["failure"] = (payload or {}).get("error") or "workload did not emit valid JSON"
    result["peak_rss_bytes"] = max(
        int(result.get("peak_rss_bytes") or 0),
        int(result.get("monitor_peak_rss_bytes") or 0),
    )
    return result


def _write_trial(handle: Any, trial: dict[str, Any]) -> None:
    handle.write(json.dumps(trial, sort_keys=True) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def _read_existing_trials(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    trials: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                try:
                    trials.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return trials


def _parity(
    mode: str,
    trials: Sequence[dict[str, Any]],
    *,
    profile: str,
    fixture_root: Path,
    run_dir: Path,
) -> dict[str, Any]:
    completed = [trial for trial in trials if trial.get("status") == "completed"]
    by_tool: dict[str, list[dict[str, Any]]] = {}
    for trial in completed:
        by_tool.setdefault(str(trial["tool"]), []).append(trial)
    result: dict[str, Any] = {}
    reference_records: list[dict[str, Any]] | None = None
    if mode == "equivalent" and profile in ("many-small-text", "jsonl-training", "csv-transform"):
        python_trials = by_tool.get("python")
        if python_trials:
            reference_path = Path(python_trials[0].get("output_path", ""))
            if reference_path.is_file():
                reference_records = load_jsonl(reference_path)
        if reference_records is None:
            reference_path = run_dir / "reference.jsonl"
            command = [
                sys.executable,
                str(BENCHMARKS / "workloads.py"),
                "--tool",
                "python",
                "--profile",
                profile,
                "--input",
                str(fixture_root / "input"),
                "--output",
                str(reference_path),
            ]
            hidden = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=3600, check=False)
            if hidden.returncode == 0 and reference_path.is_file():
                reference_records = load_jsonl(reference_path)
    for tool, runs in by_tool.items():
        if reference_records is not None:
            first = runs[0]
            actual_path = Path(first.get("output_path", ""))
            if not actual_path.is_file() and tool == "datum":
                actual_path = _datum_output(Path(first.get("output_directory", ""))) / "dataset.jsonl"
            if not actual_path.is_file():
                result[tool] = {"content_match": False, "error": "output unavailable"}
                continue
            actual_records = load_jsonl(actual_path) if tool != "datum" else output_records(
                Path(first.get("output_directory", ""))
            )
            expected_keys = {canonical_record(record) for record in reference_records}
            actual_keys = [canonical_record(record) for record in actual_records]
            actual_set = set(actual_keys)
            missing = expected_keys - actual_set
            extra = actual_set - expected_keys
            result[tool] = {
                "reference": "python",
                "expected_records": len(expected_keys),
                "actual_records": len(actual_keys),
                "missing": len(missing),
                "extra": len(extra),
                "duplicates": len(actual_keys) - len(actual_set),
                "content_match": not missing and not extra and len(actual_keys) == len(actual_set),
                "missing_samples": sorted(missing)[:3],
                "extra_samples": sorted(extra)[:3],
            }
    return result


def _determinism(trials: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_mode: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for trial in trials:
        if trial.get("status") == "completed":
            by_mode.setdefault(str(trial.get("mode", "equivalent")), {}).setdefault(
                str(trial["tool"]), []
            ).append(trial)
    result: dict[str, Any] = {}
    for mode, tools in by_mode.items():
        result[mode] = {}
        for tool, runs in tools.items():
            hashes = [run.get("records_hash") for run in runs if run.get("records_hash")]
            counts = [run.get("records") for run in runs if run.get("records") is not None]
            result[mode][tool] = {
                "runs": len(runs),
                "record_counts": counts,
                "record_counts_stable": len(set(counts)) == 1 if counts else None,
                "output_stable": len(set(hashes)) == 1 if hashes else None,
            }
    return result


def _report_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Benchmark run `{report['run_id']}`",
        "",
        f"- Source commit: `{(report.get('source') or {}).get('commit')}`",
        f"- Profile/tier: `{report['fixture'].get('profile')}` / `{report['fixture'].get('plan', {}).get('tier')}`",
        f"- Fixture: {report['fixture'].get('input_files')} files, {report['fixture'].get('input_bytes')} bytes",
        "",
        "| Mode | Tool | Runs | Median s | Min s | Max s | IQR s | Peak MiB | Records | Reference match |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for mode, tools in report.get("summary", {}).items():
        for tool, entry in tools.items():
            parity = (report.get("parity") or {}).get(mode, {}).get(tool, {})
            match = parity.get("content_match") if isinstance(parity, dict) else None
            peak = entry.get("peak_rss_bytes_max")
            lines.append(
                "| {mode} | {tool} | {runs} | {median} | {minimum} | {maximum} | {iqr} | {peak} | {records} | {match} |".format(
                    mode=mode,
                    tool=tool,
                    runs=entry.get("completed", 0),
                    median=entry.get("wall_seconds_median", "-"),
                    minimum=entry.get("wall_seconds_min", "-"),
                    maximum=entry.get("wall_seconds_max", "-"),
                    iqr=entry.get("wall_seconds_iqr", "-"),
                    peak=round(peak / (1024 * 1024), 2) if peak else "-",
                    records=entry.get("records", "-"),
                    match=match if match is not None else "-",
                )
            )
    unsupported = report.get("unsupported") or []
    if unsupported:
        lines.extend(["", "## Skipped tools", ""])
        for item in unsupported:
            lines.append(f"- `{item.get('mode')}` / `{item.get('tool')}`: {item.get('failure')}")
    failures = report.get("failures") or []
    if failures:
        lines.extend(["", "## Preserved failures", ""])
        for failure in failures:
            lines.append(
                f"- `{failure.get('mode')}` / `{failure.get('tool')}` rep {failure.get('repetition')}: "
                f"{failure.get('status')} — {failure.get('failure') or failure.get('stderr_tail', '')[-300:]}"
            )
    lines.extend(
        [
            "",
            "Wall time includes process startup and teardown. DATUM performs a full build with validation, manifest, quality, and verification; competitor rows are the declared transform only unless the row is marked workflow.",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> int:
    if args.repetitions < 1:
        raise ValueError("--repetitions must be positive")
    if args.profile not in PROFILES:
        raise ValueError(f"unknown profile: {args.profile}")
    tier = args.tier.upper()
    if tier not in TIER_BYTES:
        raise ValueError(f"unknown tier: {args.tier}")
    run_id = args.run_id or f"{_utc_stamp()}-{args.profile}-{tier}"
    runs_root = Path(args.runs_dir).expanduser().resolve()
    run_dir = runs_root / run_id
    if run_dir.exists() and not args.resume:
        raise FileExistsError(f"run directory exists; pass --resume or choose --run-id: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    fixture_root = (
        Path(args.fixture_dir).expanduser().resolve()
        if args.fixture_dir
        else run_dir / "fixture"
    )
    target_bytes = args.target_bytes or TIER_BYTES[tier]
    resources = _resource_check(
        target_bytes,
        force=args.allow_low_resource,
        minimum_disk_mib=args.min_free_disk_mib,
    )
    if not resources["allowed"]:
        payload = {
            "run_schema_version": 1,
            "run_id": run_id,
            "created_at": _utc_iso(),
            "source": _source(),
            "environment": _environment(),
            "settings": vars(args),
            "fixture": None,
            "resource_check": resources,
            "trials": [],
            "summary": {},
            "parity": {},
            "failures": [],
            "ok": False,
            "skipped": "insufficient memory or disk; rerun with --allow-low-resource to override",
        }
        (run_dir / "report.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(payload["skipped"], file=sys.stderr)
        return 3
    manifest = generate(
        args.profile,
        fixture_root,
        tier=tier,
        target_bytes=args.target_bytes,
        rows=args.rows,
        files=args.files,
        seed=args.seed,
        columns=args.columns,
        hash_fixture=args.hash_fixture,
        reuse=True,
    )
    manifest = dict(manifest)
    manifest["path"] = str(fixture_root)
    available = _available_tools()
    requested = [tool.strip() for tool in args.tools.split(",") if tool.strip()] if args.tools else []
    if requested and "datum" not in requested:
        requested.insert(0, "datum")
    modes = ["equivalent", "workflow"] if args.mode == "both" else [args.mode]
    tool_startup = _startup_probe(
        [tool for tool in TOOL_ORDER if tool in requested or not requested],
        timeout=min(args.timeout, 120),
    )
    report: dict[str, Any] = {
        "run_schema_version": 1,
        "run_id": run_id,
        "created_at": _utc_iso(),
        "source": _source(),
        "environment": _environment(),
        "settings": vars(args),
        "resource_check": resources,
        "fixture": manifest,
        "tools_available": available,
        "tool_startup_seconds": tool_startup,
        "trials": [],
        "warmups": [],
        "summary": {},
        "parity": {},
        "determinism": {},
        "failures": [],
        "ok": False,
    }
    trials_path = run_dir / "trials.jsonl"
    existing = _read_existing_trials(trials_path) if args.resume else []
    existing_by_key = {_trial_key(trial): trial for trial in existing}
    report["trials"] = list(existing)
    report["warmups"] = []
    failures: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    if args.warmups and not existing:
        warmup_path = run_dir / "warmups.jsonl"
        with warmup_path.open("w", encoding="utf-8") as warmup_handle:
            for mode in modes:
                supported = list(PROFILE_TOOLS.get(args.profile, {}).get(mode, WORKFLOW_TOOLS))
                if mode == "workflow":
                    supported = list(WORKFLOW_TOOLS)
                tools = [tool for tool in TOOL_ORDER if tool in supported and (not requested or tool in requested)]
                for tool in tools:
                    if not available.get(tool, False):
                        continue
                    output_dir = run_dir / "warmups" / mode / tool
                    if output_dir.exists():
                        shutil.rmtree(output_dir)
                    try:
                        if tool == "datum":
                            warmup = _run_datum(
                                fixture_root,
                                output_dir,
                                mode,
                                timeout=args.timeout,
                                verify=False,
                            )
                        else:
                            warmup = _run_competitor(
                                tool,
                                args.profile,
                                fixture_root,
                                output_dir,
                                timeout=args.timeout,
                            )
                    except Exception as error:  # noqa: BLE001
                        warmup = {"tool": tool, "status": "failed", "failure": str(error)}
                    warmup.update({"mode": mode, "tool": tool, "phase": "warmup"})
                    warmup_handle.write(json.dumps(warmup, sort_keys=True) + "\n")
                    warmup_handle.flush()
                    report["warmups"].append(warmup)
                    if warmup.get("status") != "completed":
                        failures.append(warmup)
    with trials_path.open("a" if args.resume else "w", encoding="utf-8") as trial_handle:
        for mode in modes:
            supported = list(PROFILE_TOOLS.get(args.profile, {}).get(mode, WORKFLOW_TOOLS))
            if mode == "workflow":
                supported = list(WORKFLOW_TOOLS)
            tools = [tool for tool in TOOL_ORDER if tool in supported and (not requested or tool in requested)]
            if not tools:
                report["skipped_modes"] = report.get("skipped_modes", []) + [
                    {"mode": mode, "reason": "no requested tools support this mode/profile"}
                ]
                continue
            for repetition in range(1, args.repetitions + 1):
                ordered = tools[repetition % len(tools):] + tools[:repetition % len(tools)]
                for tool in ordered:
                    key = (mode, tool, repetition)
                    if key in existing_by_key and existing_by_key[key].get("status") == "completed":
                        continue
                    if not available.get(tool, False):
                        skipped = {
                            "mode": mode,
                            "tool": tool,
                            "repetition": repetition,
                            "status": "skipped",
                            "failure": f"{tool} is not installed",
                        }
                        trial_handle.write(json.dumps(skipped, sort_keys=True) + "\n")
                        trial_handle.flush()
                        unsupported.append(skipped)
                        continue
                    output_dir = run_dir / "outputs" / mode / tool / f"rep-{repetition:02d}"
                    if output_dir.exists():
                        shutil.rmtree(output_dir)
                    started = time.perf_counter()
                    try:
                        if tool == "datum":
                            trial = _run_datum(
                                fixture_root,
                                output_dir,
                                mode,
                                timeout=args.timeout,
                                verify=args.verify,
                            )
                        else:
                            trial = _run_competitor(
                                tool,
                                args.profile,
                                fixture_root,
                                output_dir,
                                timeout=args.timeout,
                            )
                    except Exception as error:  # noqa: BLE001 - preserve the failure
                        trial = {
                            "tool": tool,
                            "kind": "controller",
                            "status": "failed",
                            "failure": f"{type(error).__name__}: {error}",
                            "stderr_tail": traceback.format_exc()[-2000:],
                        }
                    trial.update(
                        {
                            "mode": mode,
                            "repetition": repetition,
                            "recorded_at": _utc_iso(),
                            "controller_wall_seconds": round(time.perf_counter() - started, 6),
                            "output_dir": str(output_dir),
                        }
                    )
                    if trial.get("status") != "completed":
                        failures.append(trial)
                    _write_trial(trial_handle, trial)
                    report["trials"].append(trial)
                    print(
                        f"{mode:10s} {tool:8s} rep {repetition:02d}: {trial.get('status')} "
                        f"{trial.get('wall_seconds', 0):.3f}s",
                        flush=True,
                    )
    report["trials"] = [trial for trial in report["trials"] if trial.get("status") != "skipped"]
    report["summary"] = summarize_trials(report["trials"])
    for mode in modes:
        mode_trials = [trial for trial in report["trials"] if trial.get("mode") == mode]
        report["parity"][mode] = _parity(
            mode,
            mode_trials,
            profile=args.profile,
            fixture_root=fixture_root,
            run_dir=run_dir,
        )
    report["determinism"] = _determinism(report["trials"])
    report["failures"] = failures
    report["unsupported"] = unsupported
    datum_failures = [
        failure
        for failure in failures
        if failure.get("tool") == "datum" and failure.get("status") not in ("skipped",)
    ]
    equivalent_parity = report["parity"].get("equivalent") or {}
    parity_failures = [
        tool for tool, result in equivalent_parity.items() if isinstance(result, dict) and result.get("content_match") is False
    ]
    report["partial"] = bool(failures)
    report["ok"] = bool(report["summary"]) and not datum_failures and not parity_failures
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / "report.md").write_text(_report_markdown(report), encoding="utf-8")
    validation = validate_report(report, fixtures_root=runs_root)
    report["validation"] = validation
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"run_id": run_id, "report": str(report_path), "ok": report["ok"], "validation": validation["ok"]}))
    return 0 if report["ok"] else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a large-scale DATUM benchmark experiment.")
    parser.add_argument("--profile", choices=PROFILES, required=True)
    parser.add_argument("--tier", default="A", choices=sorted(TIER_BYTES))
    parser.add_argument("--mode", choices=["equivalent", "workflow", "both"], default="both")
    parser.add_argument("--tools", help="comma-separated subset; DATUM is always included")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("--runs-dir", default=str(BENCHMARKS / "runs"))
    parser.add_argument("--fixture-dir")
    parser.add_argument("--run-id")
    parser.add_argument("--target-bytes", type=int)
    parser.add_argument("--rows", type=int)
    parser.add_argument("--files", type=int)
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--hash-fixture", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--verify", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--allow-low-resource", action="store_true")
    parser.add_argument("--min-free-disk-mib", type=int, default=512)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (OSError, ValueError, FileExistsError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
