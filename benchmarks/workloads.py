"""Independent workloads used by the large-scale benchmark runner."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Sequence

TEXT_PROFILES = {"many-small-text"}
COMPLETION_PROFILES = {"csv-transform", "jsonl-training"}


def _peak_rss_bytes() -> int:
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
        return 0


def normalize_text(text: str) -> str:
    result = text.replace("\r\n", "\n").replace("\r", "\n")
    result = "".join(
        character
        for character in result
        if character in "\n\t" or unicodedata.category(character) != "Cc"
    )
    return result.strip()


def canonical(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _output_hash(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _write_records(path: Path, records: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(canonical(record) + "\n")
            count += 1
    return count


def _text_paths(input_dir: Path) -> list[Path]:
    return sorted(input_dir.rglob("*.txt"), key=lambda item: item.relative_to(input_dir).as_posix())


def _jsonl_paths(input_dir: Path) -> list[Path]:
    return sorted(input_dir.rglob("*.jsonl"), key=lambda item: item.relative_to(input_dir).as_posix())


def _csv_paths(input_dir: Path) -> list[Path]:
    return sorted(input_dir.rglob("*.csv"), key=lambda item: item.relative_to(input_dir).as_posix())


def _text_records(paths: Sequence[Path]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    records: list[dict[str, Any]] = []
    for path in paths:
        text = normalize_text(path.read_bytes().decode("utf-8", "replace"))
        if not text or text in seen:
            continue
        seen.add(text)
        records.append({"text": text})
    records.sort(key=lambda item: item["text"])
    return records


def _completion_record(
    prompt: Any,
    completion: Any,
    *,
    minimum: int = 1,
    maximum: int = 12000,
) -> dict[str, str] | None:
    if not isinstance(prompt, str) or not isinstance(completion, str):
        return None
    prompt = normalize_text(prompt)
    completion = normalize_text(completion)
    length = len(prompt) + len(completion)
    if length < minimum or length > maximum:
        return None
    return {"prompt": prompt, "completion": completion}


def _jsonl_records(paths: Sequence[Path], maximum: int = 12000) -> list[dict[str, Any]]:
    seen: set[str] = set()
    records: list[dict[str, Any]] = []
    for path in paths:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                label = row.get("label")
                if label not in ("supported", "review"):
                    continue
                record = _completion_record(
                    row.get("prompt"),
                    row.get("completion"),
                    minimum=5,
                    maximum=maximum,
                )
                if record is None:
                    continue
                record["label"] = label
                key = canonical(record)
                if key in seen:
                    continue
                seen.add(key)
                records.append(record)
    records.sort(key=lambda item: (item["prompt"], item["completion"], item["label"]))
    return records


def _csv_records(paths: Sequence[Path], maximum: int = 1000000) -> list[dict[str, Any]]:
    seen: set[str] = set()
    records: list[dict[str, Any]] = []
    for path in paths:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            for row in csv.DictReader(handle):
                record = _completion_record(
                    row.get("prompt"),
                    row.get("completion"),
                    minimum=1,
                    maximum=maximum,
                )
                if record is None:
                    continue
                key = canonical(record)
                if key in seen:
                    continue
                seen.add(key)
                records.append(record)
    records.sort(key=lambda item: (item["prompt"], item["completion"]))
    return records


def _path_for(profile: str, tool: str, input_dir: Path) -> list[Path]:
    if profile == "many-small-text":
        return _text_paths(input_dir)
    if profile == "jsonl-training":
        return _jsonl_paths(input_dir)
    if profile == "csv-transform":
        return _csv_paths(input_dir)
    raise ValueError(f"profile {profile!r} has no supported equivalent workload")


def _reference(profile: str, input_dir: Path) -> list[dict[str, Any]]:
    paths = _path_for(profile, "python", input_dir)
    if profile == "many-small-text":
        return _text_records(paths)
    if profile == "jsonl-training":
        return _jsonl_records(paths)
    return _csv_records(paths)


def _pandas(profile: str, input_dir: Path) -> list[dict[str, Any]]:
    import pandas as pd

    paths = _path_for(profile, "pandas", input_dir)
    if profile == "many-small-text":
        frame = pd.DataFrame({"text": [path.read_text(encoding="utf-8", errors="replace") for path in paths]})
        frame["text"] = frame["text"].str.replace("\r\n", "\n", regex=False).str.replace("\r", "\n", regex=False)
        frame["text"] = frame["text"].str.strip()
        frame = frame[frame["text"].str.len() > 0]
        frame = frame.drop_duplicates(subset=["text"], keep="first")
        frame = frame.sort_values("text")
        return [{"text": value} for value in frame["text"].tolist()]
    if profile == "jsonl-training":
        rows: list[dict[str, Any]] = []
        for path in paths:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict) or row.get("label") not in ("supported", "review"):
                        continue
                    record = _completion_record(
                        row.get("prompt"),
                        row.get("completion"),
                        minimum=5,
                        maximum=12000,
                    )
                    if record is not None:
                        record["label"] = row["label"]
                        rows.append(record)
        frame = pd.DataFrame(rows, columns=["prompt", "completion", "label"])
        frame = frame[["prompt", "completion", "label"]].copy()
        frame["label"] = frame["label"].astype(str)
    else:
        frame = pd.read_csv(paths[0], dtype=str, keep_default_na=False)
        frame = frame[["prompt", "completion"]].copy()
    for column in ("prompt", "completion"):
        frame[column] = frame[column].astype(str)
        frame[column] = frame[column].str.replace("\r\n", "\n", regex=False).str.replace("\r", "\n", regex=False)
        frame[column] = frame[column].str.strip()
    frame = frame[(frame["prompt"].str.len() + frame["completion"].str.len()) > 0]
    frame = frame[(frame["prompt"].str.len() + frame["completion"].str.len()) <= (12000 if profile == "jsonl-training" else 1000000)]
    subset = ["prompt", "completion", "label"] if profile == "jsonl-training" else ["prompt", "completion"]
    frame = frame.drop_duplicates(subset=subset, keep="first")
    frame = frame.sort_values(subset)
    return frame[subset].to_dict(orient="records")


def _duckdb(profile: str, input_dir: Path, output_path: Path) -> list[dict[str, Any]]:
    import duckdb

    connection = duckdb.connect(database=":memory:")
    temporary = output_path.with_suffix(".duckdb-rows.jsonl")
    try:
        if profile == "many-small-text":
            pattern = str(input_dir / "**" / "*.txt")
            connection.execute(
                "COPY ("
                "SELECT DISTINCT trim(regexp_replace(regexp_replace(content, '\\r\\n', '\\n', 'g'), '\\r', '\\n', 'g')) AS text "
                "FROM read_text(?) WHERE length(trim(content)) > 0 ORDER BY text"
                f") TO '{str(temporary).replace(chr(39), chr(39) * 2)}' (FORMAT JSON, ARRAY false)",
                [pattern],
            )
        elif profile == "jsonl-training":
            pattern = str(input_dir / "**" / "*.jsonl")
            escaped = str(temporary).replace("'", "''")
            connection.execute(
                "COPY ("
                "SELECT prompt, completion, label FROM ("
                "SELECT coalesce(trim(CAST(prompt AS VARCHAR)), '') AS prompt, "
                "coalesce(trim(CAST(completion AS VARCHAR)), '') AS completion, "
                "CAST(label AS VARCHAR) AS label "
                "FROM read_json(?, format='newline_delimited', "
                "columns={'prompt': 'VARCHAR', 'completion': 'VARCHAR', 'label': 'VARCHAR'}, ignore_errors=true)"
                ") WHERE label IN ('supported', 'review') "
                "AND length(prompt) + length(completion) >= 5 "
                "AND length(prompt) + length(completion) <= 12000 "
                "QUALIFY row_number() OVER (PARTITION BY prompt, completion, label ORDER BY prompt) = 1 "
                "ORDER BY prompt, completion, label"
                f") TO '{escaped}' (FORMAT JSON, ARRAY false)",
                [pattern],
            )
        else:
            pattern = str(input_dir / "**" / "*.csv")
            escaped = str(temporary).replace("'", "''")
            connection.execute(
                "COPY ("
                "SELECT prompt, completion FROM ("
                "SELECT coalesce(trim(CAST(prompt AS VARCHAR)), '') AS prompt, "
                "coalesce(trim(CAST(completion AS VARCHAR)), '') AS completion, "
                "row_number() OVER (PARTITION BY coalesce(trim(CAST(prompt AS VARCHAR)), ''), "
                "coalesce(trim(CAST(completion AS VARCHAR)), '') ORDER BY 1) AS rn "
                "FROM read_csv_auto(?, header=true, all_varchar=true)"
                ") WHERE length(prompt) + length(completion) > 0 AND rn = 1 "
                "ORDER BY prompt, completion"
                f") TO '{escaped}' (FORMAT JSON, ARRAY false)",
                [pattern],
            )
    finally:
        connection.close()
    records: list[dict[str, Any]] = []
    with temporary.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    temporary.unlink(missing_ok=True)
    return records


def _polars(profile: str, input_dir: Path) -> list[dict[str, Any]]:
    import polars as pl

    paths = _path_for(profile, "polars", input_dir)
    if profile == "many-small-text":
        texts = [
            normalize_text(path.read_bytes().decode("utf-8", "replace"))
            for path in paths
        ]
        frame = pl.DataFrame({"text": texts})
        frame = frame.filter(pl.col("text").str.len_chars() > 0)
        frame = frame.unique(subset=["text"], keep="first").sort("text")
        return [{"text": value} for value in frame["text"].to_list()]
    if profile == "jsonl-training":
        rows: list[dict[str, Any]] = []
        for path in paths:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict) or row.get("label") not in ("supported", "review"):
                        continue
                    record = _completion_record(
                        row.get("prompt"),
                        row.get("completion"),
                        minimum=5,
                        maximum=12000,
                    )
                    if record is not None:
                        record["label"] = row["label"]
                        rows.append(record)
        frame = pl.DataFrame(rows, schema={"prompt": pl.Utf8, "completion": pl.Utf8, "label": pl.Utf8})
    else:
        frame = pl.read_csv(paths[0], infer_schema_length=0, null_values=[])
    frame = frame.select(["prompt", "completion", "label"] if profile == "jsonl-training" else ["prompt", "completion"])
    frame = frame.with_columns(
        [
            pl.col("prompt").cast(pl.Utf8).str.replace_all("\r\n", "\n").str.replace_all("\r", "\n").str.strip_chars(),
            pl.col("completion").cast(pl.Utf8).str.replace_all("\r\n", "\n").str.replace_all("\r", "\n").str.strip_chars(),
        ]
    )
    frame = frame.filter(
        (pl.col("prompt").str.len_chars() + pl.col("completion").str.len_chars()) > 0
    )
    frame = frame.filter(
        (pl.col("prompt").str.len_chars() + pl.col("completion").str.len_chars())
        <= (12000 if profile == "jsonl-training" else 1000000)
    )
    subset = ["prompt", "completion", "label"] if profile == "jsonl-training" else ["prompt", "completion"]
    frame = frame.unique(subset=subset, keep="first").sort(subset)
    return frame.to_dicts()


def run(tool: str, profile: str, input_dir: Path, output_path: Path) -> dict[str, Any]:
    if profile not in TEXT_PROFILES | COMPLETION_PROFILES:
        raise ValueError(f"profile {profile!r} is DATUM-only")
    started = time.perf_counter()
    versions: dict[str, str] = {}
    if tool == "python":
        records = _reference(profile, input_dir)
        versions["python"] = sys.version.split()[0]
    elif tool == "pandas":
        import pandas as pd

        records = _pandas(profile, input_dir)
        versions["pandas"] = pd.__version__
    elif tool == "duckdb":
        import duckdb

        records = _duckdb(profile, input_dir, output_path)
        versions["duckdb"] = duckdb.__version__
    elif tool == "polars":
        import polars as pl

        records = _polars(profile, input_dir)
        versions["polars"] = pl.__version__
    else:
        raise ValueError(f"unknown tool {tool!r}")
    records = [
        record
        for record in records
        if set(record) in ({"text"}, {"prompt", "completion"}, {"prompt", "completion", "label"})
    ]
    count = _write_records(output_path, records)
    duration = time.perf_counter() - started
    digest, size = _output_hash(output_path)
    return {
        "tool": tool,
        "profile": profile,
        "status": "ok",
        "records": count,
        "output_bytes": size,
        "output_sha256": digest,
        "work_seconds": round(duration, 6),
        "peak_rss_bytes": _peak_rss_bytes(),
        "versions": versions,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an independent benchmark workload.")
    parser.add_argument("--tool", required=True, choices=["python", "pandas", "duckdb", "polars"])
    parser.add_argument("--profile", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        result = run(args.tool, args.profile, Path(args.input), Path(args.output))
    except Exception as error:  # noqa: BLE001 - the runner records tool failures verbatim
        print(
            json.dumps(
                {
                    "tool": args.tool,
                    "profile": args.profile,
                    "status": "failed",
                    "error": f"{type(error).__name__}: {error}",
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
