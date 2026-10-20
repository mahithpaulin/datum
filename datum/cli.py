"""DATUM command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import traceback
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

from . import MANIFEST_SCHEMA_VERSION, OUTPUT_SCHEMA_VERSION, QUALITY_SCHEMA_VERSION, __version__
from .build import build_summary_text, run_build, validate_build_config
from .config import Config, SourceOptions, load_config
from .discovery import discover
from .errors import ConfigError, DatumError, UsageError, VerificationError
from .filetypes import detect_file, format_breakdown
from .manifest import summarize_manifest
from .util import human_bytes
from .verify import find_manifest, verify_build


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise UsageError(message)


def _json_dump(value: Any, stream: Any = None) -> None:
    target = stream or sys.stdout
    target.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n")


def _default_config() -> str:
    return "datum.toml"


def _load(path: str, *, allow_missing_source: bool = False) -> Config:
    return load_config(path, allow_missing_source=allow_missing_source)


class _Progress:
    def __init__(self) -> None:
        self.enabled = sys.stderr.isatty()
        self.started = 0.0
        self.last = 0.0

    def __call__(self, completed: int, total: int, result: Any) -> None:
        import time

        if not self.enabled:
            return
        now = time.perf_counter()
        if self.started == 0.0:
            self.started = now
        if completed < total and now - self.last < 0.15:
            return
        self.last = now
        elapsed = max(0.000001, now - self.started)
        rate = completed / elapsed
        status = result.status
        line = (
            f"\r[{completed}/{total}] {status:<9} "
            f"{result.entry.relative_path[:60]:<60} {rate:6.1f} files/s"
        )
        sys.stderr.write(line[:180])
        sys.stderr.flush()
        if completed >= total:
            sys.stderr.write("\n")
            sys.stderr.flush()


def _cmd_build(args: argparse.Namespace) -> int:
    if args.workers is not None and args.workers < 0:
        raise UsageError("--workers must be zero or positive")
    config = _load(args.config)
    config = config.with_overrides(
        output=Path(args.output) if args.output else None,
        workers=args.workers,
        strict=True if args.strict else None,
        cache_enabled=False if args.no_cache else None,
        cache_dir=Path(args.cache_dir) if args.cache_dir else None,
        progress=False if (args.no_progress or args.json or args.quiet) else None,
    )
    if args.dry_run:
        validate_build_config(config)
        discovered = discover(config.source)
        payload = {
            "ok": True,
            "dry_run": True,
            "config": str(config.path),
            "source": str(config.source.path),
            "output": str(config.output.directory),
            "discovered_files": len(discovered.files),
            "input_bytes": discovered.total_bytes,
            "walk_issues": len(discovered.walk_issues),
            "schema": config.schema.name,
        }
        if args.json:
            _json_dump(payload)
        elif not args.quiet:
            print(f"Configuration valid: {config.path}")
            print(f"Source: {config.source.path}")
            print(f"Files: {len(discovered.files)} ({human_bytes(discovered.total_bytes)})")
            print(f"Output: {config.output.directory}")
            print(f"Schema: {config.schema.name} v{config.schema.version}")
        return 0
    progress = _Progress() if config.runtime.progress and not args.json and not args.quiet else None
    result = run_build(config, progress=progress)
    if args.json:
        payload = result.to_dict()
        payload["ok"] = result.status == "passed"
        _json_dump(payload)
    elif not args.quiet:
        print(build_summary_text(result))
        if result.status != "passed":
            print("\nQuality thresholds failed. The build was retained under failed/ and current/ was not replaced.")
            print(result.quality_path)
    return 0 if result.status == "passed" else 2


def _inspect_config(path: str, source_override: str | None) -> tuple[Any, SourceOptions]:
    if source_override:
        source = Path(source_override).expanduser().resolve()
        if path:
            config = _load(path)
            source_options = replace(config.source, path=source)
            return replace(config, source=source_options), source_options
        source_options = SourceOptions(path=source)
        return SimpleNamespace(source=source_options), source_options
    if not path:
        path = _default_config()
    config = _load(path)
    return config, config.source

def _cmd_inspect(args: argparse.Namespace) -> int:
    config, source = _inspect_config(args.config or "", args.source)
    if not source.path.exists() or not source.path.is_dir():
        raise ConfigError(f"source path is not a directory: {source.path}")
    discovered = discover(source)
    detections = []
    for entry in discovered.files:
        detection = detect_file(entry.path, config)
        detections.append((entry, detection))
    formats = format_breakdown([detection for _entry, detection in detections])
    unsupported = [
        {
            "path": entry.relative_path,
            "size": entry.size,
            "format": detection.format,
            "reason": detection.reason,
            "error": detection.error,
        }
        for entry, detection in detections
        if not detection.supported
    ]
    limit = len(detections) if args.all else args.limit
    examples = [
        {
            "path": entry.relative_path,
            "bytes": entry.size,
            "format": detection.format,
            "adapter": detection.adapter,
            "supported": detection.supported,
            "reason": detection.reason,
        }
        for entry, detection in detections[:limit]
    ]
    scope = "all" if args.all or len(detections) <= limit else f"first {limit}"
    payload = {
        "source": str(config.source.path),
        "files": len(detections),
        "input_bytes": discovered.total_bytes,
        "formats": formats,
        "supported": sum(1 for _entry, detection in detections if detection.supported),
        "unsupported": unsupported,
        "walk_issues": [{"path": issue.path, "message": issue.message} for issue in discovered.walk_issues],
        "excluded_by_config": discovered.excluded,
        "hidden_skipped": discovered.hidden,
        "symlinks_skipped": discovered.symlinks_skipped,
        "examples": examples,
        "examples_scope": scope,
    }
    if args.json:
        _json_dump(payload)
        return 0
    print(f"Source: {config.source.path}")
    print(f"Files: {len(detections)} ({human_bytes(discovered.total_bytes)})")
    print("Formats: " + ", ".join(f"{name}={count}" for name, count in formats.items()))
    print(
        f"Skipped by discovery: excluded={discovered.excluded} hidden={discovered.hidden} "
        f"symlinks={discovered.symlinks_skipped} non-regular={discovered.non_regular_skipped}"
    )
    if discovered.walk_issues:
        print(f"Walk issues: {len(discovered.walk_issues)}")
    if unsupported:
        print("\nUnsupported:")
        for item in unsupported[: args.limit]:
            print(f"  {item['path']} ({human_bytes(item['size'])}): {item['reason']}")
        if len(unsupported) > args.limit:
            print(f"  ... {len(unsupported) - args.limit} more")
    if examples:
        print(f"\nFiles ({scope}):")
        for item in examples:
            state = item["format"] if item["supported"] else f"unsupported: {item['reason']}"
            print(f"  {item['path']} ({human_bytes(item['bytes'])}): {state}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    config = _load(args.config)
    schema = validate_build_config(config)
    payload = {
        "ok": True,
        "config": str(config.path),
        "source": str(config.source.path),
        "output": str(config.output.directory),
        "schema": {"name": schema.spec.name, "version": schema.spec.version, "kind": schema.spec.kind},
        "config_fingerprint": config.config_fingerprint(),
        "pipeline_fingerprint": config.pipeline_fingerprint(),
    }
    if args.json:
        _json_dump(payload)
    else:
        print("Configuration is valid.")
        print(f"Config: {config.path}")
        print(f"Source: {config.source.path}")
        print(f"Output: {config.output.directory}")
        print(f"Schema: {schema.spec.name} v{schema.spec.version} ({schema.spec.kind})")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    report = verify_build(args.path, check_sources=args.sources)
    if args.json:
        _json_dump(report.to_dict())
    else:
        print(f"Verification: {'PASS' if report.ok else 'FAIL'}")
        if report.manifest_path is not None:
            print(f"Manifest: {report.manifest_path}")
        if report.output_identity:
            print(f"Output identity: {report.output_identity}")
        for check in report.checks:
            mark = "PASS" if check["passed"] else "FAIL"
            detail = f"  {check['detail']}" if check.get("detail") else ""
            print(f"  [{mark}] {check['name']}{detail}")
        if report.warnings:
            for warning in report.warnings:
                print(f"  warning: {warning}")
    return 0 if report.ok else 1


def _cmd_manifest(args: argparse.Namespace) -> int:
    path = find_manifest(args.path)
    with path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    summary = summarize_manifest(manifest)
    if args.json:
        _json_dump(manifest if args.full else summary)
    else:
        print(f"Build ID: {summary['build_id']}")
        print(f"Status: {summary['status']} (quality: {summary['quality_status']})")
        print(f"Manifest schema: {summary['manifest_schema_version']}")
        print(f"Output identity: {summary['output_identity']}")
        print(f"Schema: {summary['schema']['name']} v{summary['schema']['version']}")
        print(f"Inputs: {summary['inputs']}; exported records: {summary['exported_records']}")
        print(f"Started: {summary['started_at']}")
        print(f"Finished: {summary['finished_at']}")
        print("Outputs:")
        for item in summary["outputs"]:
            print(
                f"  {item['path']}: {item['records']} records, {item['bytes']} bytes, "
                f"sha256={item['sha256']}"
            )
        if args.full:
            print("\nFull manifest:")
            _json_dump(manifest)
    return 0


def _cmd_quality(args: argparse.Namespace) -> int:
    manifest_path = find_manifest(args.path)
    quality_path = manifest_path.parent / "quality.json"
    if not quality_path.is_file():
        raise VerificationError(f"quality.json not found under {manifest_path.parent}")
    with quality_path.open("r", encoding="utf-8") as handle:
        quality = json.load(handle)
    passed = quality.get("status") == "passed"
    failed_invariants = [
        item for item in quality.get("invariants", []) if not item.get("passed", False)
    ]
    summary = {
        "ok": passed,
        "build_id": quality.get("build_id"),
        "status": quality.get("status"),
        "records": quality.get("records", {}),
        "rejected": quality.get("rejected", {}),
        "errors": quality.get("errors", {}),
        "thresholds": quality.get("thresholds", []),
        "failed_invariants": failed_invariants,
        "quality_path": str(quality_path),
        "rejected_log": str(quality_path.parent / "rejected.jsonl"),
    }
    if args.json:
        _json_dump(quality if args.full else summary)
        return 0 if passed else 2
    print(f"Quality: {'PASS' if passed else 'FAIL'}")
    print(f"Build ID: {summary['build_id']}")
    records = summary["records"]
    print(f"Exported: {records.get('exported', 0)}")
    print(f"Rejected: {records.get('rejected_total', 0)}")
    for rule, count in (quality.get("rejected", {}).get("by_rule") or {}).items():
        print(f"  {rule}: {count}")
    errors = summary["errors"]
    if errors.get("by_code"):
        print("Errors:")
        for code, count in errors["by_code"].items():
            print(f"  {code}: {count}")
        for item in errors.get("examples", []):
            print(f"    {item.get('path', '?')}: {item.get('message', '')}")
        if errors.get("examples_truncated"):
            print("    ... more examples in quality.json")
    failed_thresholds = [item for item in summary["thresholds"] if not item.get("passed", False)]
    if failed_thresholds:
        print("Failed thresholds:")
        for item in failed_thresholds:
            print(f"  {item['metric']}: actual={item['actual']} requirement={item['requirement']}")
    else:
        print("Thresholds: all passed")
    if failed_invariants:
        print("Failed invariants:")
        for item in failed_invariants:
            print(f"  {item['name']}: {item.get('detail', '')}")
    else:
        print("Invariants: all passed")
    print(f"Rejected log: {summary['rejected_log']}")
    if args.full:
        print("\nFull quality report:")
        _json_dump(quality)
    return 0 if passed else 2


def _safe_remove_target(path: Path, output: Path) -> Path:
    resolved = path.resolve()
    if resolved == output or not resolved.is_relative_to(output):
        raise ConfigError(f"refusing to remove path outside output.dir: {path}")
    return resolved


def _cmd_clean(args: argparse.Namespace) -> int:
    config = _load(args.config, allow_missing_source=True)
    output = Path(args.output).expanduser().resolve() if args.output else config.output.directory.resolve()
    select_all = args.all
    targets: list[tuple[str, Path]] = []
    if select_all or args.current:
        targets.append(("current", output / "current"))
    if select_all or args.previous:
        targets.append(("previous", output / "previous"))
    if select_all or args.failed:
        targets.append(("failed", output / "failed"))
    cache_path = config.cache_directory().resolve()
    if (select_all or args.cache) and cache_path.is_relative_to(output):
        targets.append(("cache", cache_path))
    staging = sorted(output.glob(".datum-staging-*")) + sorted(output.glob(".datum-spool-*"))
    if select_all:
        targets.extend((("staging", path) for path in staging))
    targets = [(name, _safe_remove_target(path, output)) for name, path in targets if path.exists()]
    if not targets:
        if args.json:
            _json_dump({"ok": True, "removed": [], "message": "nothing to remove"})
        else:
            print("Nothing to remove.")
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            raise UsageError("refusing to delete without --yes in a non-interactive session")
        print("Will remove:")
        for name, path in targets:
            print(f"  {name}: {path}")
        answer = input("Continue? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Cancelled.")
            return 0
    removed = []
    for name, path in targets:
        if path.is_symlink():
            path.unlink()
        else:
            shutil.rmtree(path)
        removed.append({"name": name, "path": str(path)})
    if args.json:
        _json_dump({"ok": True, "removed": removed})
    else:
        for item in removed:
            print(f"Removed {item['name']}: {item['path']}")
    return 0


def _cmd_benchmark(args: argparse.Namespace) -> int:
    from .benchmark import run_benchmark
    from .util import atomic_write_json

    if args.files < 1:
        raise UsageError("benchmark --files must be at least 1")
    if args.file_kib < 1:
        raise UsageError("benchmark --file-kib must be at least 1")
    if args.workers < 0:
        raise UsageError("benchmark --workers must be zero or positive")
    report = run_benchmark(
        files=args.files,
        file_kib=args.file_kib,
        workers=args.workers,
        workdir=Path(args.workdir) if args.workdir else None,
        keep=bool(args.workdir),
    )
    if args.json_out:
        atomic_write_json(Path(args.json_out).expanduser(), report)
    if args.json:
        _json_dump(report)
    else:
        fixture = report["fixture"]
        print(f"DATUM {report['datum_version']} synthetic benchmark")
        print(f"Fixture: {fixture['files']} files, {human_bytes(fixture['input_bytes'])} input")
        for run in report["runs"]:
            print(
                f"  {run['label']:<14} wall={run['wall_seconds']:.2f}s "
                f"build={float(run['build_seconds'] or 0):.2f}s "
                f"files/s={run['files_per_second']} records/s={run['exported_records_per_second']} "
                f"cache={run['cache']['hits']}h/{run['cache']['misses']}m"
            )
        print(f"Deterministic outputs: {'yes' if report['deterministic_outputs'] else 'no'}")
        print("Synthetic mechanics benchmark; not a general performance claim.")
    return 0 if report["deterministic_outputs"] else 1


def _cmd_version(_args: argparse.Namespace) -> int:
    print(f"DATUM {__version__}")
    print(f"Python {sys.version.split()[0]} on {sys.platform}")
    print(
        f"Schemas: manifest={MANIFEST_SCHEMA_VERSION} quality={QUALITY_SCHEMA_VERSION} "
        f"output={OUTPUT_SCHEMA_VERSION}"
    )
    print("Runtime dependencies: standard library only")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="datum", description="Compile local files into verified, reproducible AI-ready datasets.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="validate, compile, verify, and publish a dataset")
    build.add_argument("--config", default=_default_config(), help="TOML configuration path")
    build.add_argument("--output", help="override output.dir")
    build.add_argument("--workers", type=int, help="worker threads; 0 means automatic")
    build.add_argument("--strict", action="store_true", help="fail the build on file or malformed-input errors")
    build.add_argument("--dry-run", action="store_true", help="validate configuration and discovery without building")
    build.add_argument("--no-cache", action="store_true", help="disable the per-file extraction cache")
    build.add_argument("--cache-dir", help="override the cache directory")
    build.add_argument("--no-progress", action="store_true", help="disable progress output")
    build.add_argument("--json", action="store_true", help="emit a JSON result")
    build.add_argument("--quiet", action="store_true", help="suppress normal output")
    build.set_defaults(handler=_cmd_build)

    inspect = subparsers.add_parser("inspect", help="inspect source files and detected formats without ingesting")
    inspect.add_argument("source", nargs="?", help="source directory")
    inspect.add_argument("--config", help="read source settings from a TOML configuration")
    inspect.add_argument("--limit", type=int, default=50, help="maximum file examples to print")
    inspect.add_argument("--all", action="store_true", help="label the example list as all files")
    inspect.add_argument("--json", action="store_true", help="emit JSON")
    inspect.set_defaults(handler=_cmd_inspect)

    validate = subparsers.add_parser("validate", help="validate configuration, schema, paths, and tokenizer")
    validate.add_argument("--config", default=_default_config(), help="TOML configuration path")
    validate.add_argument("--json", action="store_true", help="emit JSON")
    validate.set_defaults(handler=_cmd_validate)

    verify = subparsers.add_parser("verify", help="verify output files, schema, counts, and manifest identity")
    verify.add_argument("path", nargs="?", default=".", help="build directory, current/, failed/ entry, or manifest")
    verify.add_argument("--sources", action="store_true", help="also verify source file hashes")
    verify.add_argument("--json", action="store_true", help="emit JSON")
    verify.set_defaults(handler=_cmd_verify)

    manifest = subparsers.add_parser("manifest", help="inspect a build manifest")
    manifest.add_argument("path", nargs="?", default=".", help="build directory or manifest path")
    manifest.add_argument("--json", action="store_true", help="emit JSON")
    manifest.add_argument("--full", action="store_true", help="include the full manifest")
    manifest.set_defaults(handler=_cmd_manifest)

    quality = subparsers.add_parser("quality", help="inspect build quality, errors, thresholds, and invariants")
    quality.add_argument("path", nargs="?", default=".", help="build directory, current/, failed/ entry, or manifest")
    quality.add_argument("--json", action="store_true", help="emit JSON")
    quality.add_argument("--full", action="store_true", help="include the full quality report")
    quality.set_defaults(handler=_cmd_quality)

    clean = subparsers.add_parser("clean", help="remove build outputs or cache after review")
    clean.add_argument("--config", default=_default_config(), help="TOML configuration path")
    clean.add_argument("--output", help="override output.dir")
    clean.add_argument("--current", action="store_true", help="remove current/")
    clean.add_argument("--previous", action="store_true", help="remove previous/")
    clean.add_argument("--failed", action="store_true", help="remove failed/ builds")
    clean.add_argument("--cache", action="store_true", help="remove the configured cache inside output.dir")
    clean.add_argument("--all", action="store_true", help="remove all known output directories and staging work")
    clean.add_argument("--yes", action="store_true", help="confirm deletion")
    clean.add_argument("--json", action="store_true", help="emit JSON")
    clean.set_defaults(handler=_cmd_clean)

    benchmark = subparsers.add_parser("benchmark", help="run a reproducible synthetic build benchmark")
    benchmark.add_argument("--files", type=int, default=32, help="number of generated documents")
    benchmark.add_argument("--file-kib", type=int, default=64, help="approximate KiB per document")
    benchmark.add_argument("--workers", type=int, default=0, help="worker threads; zero means automatic")
    benchmark.add_argument("--workdir", help="retain benchmark inputs and outputs here")
    benchmark.add_argument("--json-out", help="write the benchmark report to a file")
    benchmark.add_argument("--json", action="store_true", help="emit JSON")
    benchmark.set_defaults(handler=_cmd_benchmark)

    version = subparsers.add_parser("version", help="show versions and runtime information")
    version.set_defaults(handler=_cmd_version)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    json_requested = "--json" in (list(argv) if argv is not None else sys.argv[1:])
    try:
        args = parser.parse_args(argv)
        if getattr(args, "command", "") == "inspect" and args.limit < 1:
            raise UsageError("inspect --limit must be at least 1")
        return int(args.handler(args))
    except DatumError as error:
        if json_requested:
            _json_dump(
                {
                    "ok": False,
                    "error_type": error.error_type,
                    "message": str(error),
                    "exit_code": error.exit_code,
                }
            )
        else:
            print(f"error: {error}", file=sys.stderr)
        return error.exit_code
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 130
    except Exception as error:
        if os.environ.get("DATUM_DEBUG"):
            traceback.print_exc()
        else:
            print(f"internal error: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
