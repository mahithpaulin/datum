# Large-scale benchmark harness

This directory contains two different benchmarks:

- `datum benchmark` / `benchmarks/results/smoke.json`: a small CI regression benchmark.
- `benchmarks/run.py`: the configurable experiment controller described here.

The controller generates seeded fixtures, records every trial, supports resume, keeps
failures, and compares DATUM with an independent Python reference plus optional pandas,
DuckDB, and Polars baselines.

## Fairness boundary

DATUM runs a **complete build**: format detection, schema validation, normalization,
deduplication, chunking/splitting, JSONL export, quality accounting, manifest generation,
and post-build verification. The competitor rows run only the declared transform unless a
row is explicitly marked `workflow`.

Wall time includes Python process startup for every tool. Read the rows as a mechanics
comparison, not as an apples-to-apples product ranking.

## Requirements

- Python 3.11 or newer.
- No required third-party runtime dependency for DATUM.
- Optional baselines: `pandas`, `duckdb`, `polars`, `psutil` (RSS monitoring).
- Enough memory and disk for the selected tier. The controller refuses obviously
  undersized hosts unless `--allow-low-resource` is passed.

## Quick start

```sh
# Tier A CSV transform, Python reference plus DATUM.
python benchmarks/run.py \
  --profile csv-transform \
  --tier A \
  --mode both \
  --tools python \
  --repetitions 3 \
  --warmups 1 \
  --runs-dir benchmarks/runs

# Add pandas, DuckDB, and Polars when installed.
python benchmarks/run.py \
  --profile jsonl-training \
  --tier A \
  --mode equivalent \
  --tools pandas,duckdb,polars \
  --repetitions 5
```

The runner prints the report path on success. Generated fixtures, outputs, and raw trials
live under `benchmarks/runs/<run-id>/` and are ignored by git.

## Profiles

| Profile | What it exercises | Equivalent baselines |
| --- | --- | --- |
| `many-small-text` | Thousands of valid UTF-8 files, empties, duplicates, Unicode, unusual paths | Python, pandas, DuckDB, Polars |
| `large-text` | Fewer large documents with character chunking and overlap | DATUM only |
| `jsonl-training` | Well-formed JSONL with null-typed, missing, duplicate, schema-invalid, overlong rows | Python, pandas, DuckDB, Polars |
| `csv-transform` | Prompt/completion CSV with empties, duplicates, whitespace, Unicode | Python, pandas, DuckDB, Polars |
| `mixed-formats` | Text, Markdown, CSV, TSV, JSON, JSONL, DOCX, PDF, unsupported files | DATUM only |
| `adversarial-quality` | Corrupt files, unusual paths, malformed rows, overlong records, unsupported inputs | DATUM only |

Profiles without an equivalent baseline still run in workflow mode and record record
counts, rejection accounting, peak RSS, determinism, and verification evidence.

## Tiers

| Tier | Target input | Intended host |
| --- | ---: | --- |
| `A` | ~10 MiB | Phone or CI |
| `B` | ~100 MiB | Kaggle/CI |
| `C` | ~1 GiB | Kaggle |
| `D` | ~5 GiB | Dedicated host |
| `E` | ~10 GiB | Dedicated host |

Actual bytes are recorded in `fixture.json`; generated sizes are approximate because
empty/duplicate files, compressed DOCX/PDF, and row validation change the final total.
Generated DATUM configs raise `limits.max_file_bytes` above the tier target so the default
64 MiB safety limit does not reject legitimate large-tier inputs. Advance to a larger tier
only after the smaller tier completes without unexplained failures.

## Modes

- `equivalent`: tools execute the same declared transform. Record-level parity is checked
  against the independent Python reference where one exists.
- `workflow`: DATUM performs provenance sidecars, splitting, cache configuration, and
  independent verification. These numbers are reported separately and must not be mixed
  with the transform-only comparison.
- `both`: runs both, in separate tables.

## What is recorded

Each run directory contains:

- `fixture/fixture.json` and configs.
- `trials.jsonl`: append-only measured trials.
- `warmups.jsonl`: warm-up trials, not included in medians.
- `report.json`: manifest, environment, source commit, settings, raw trials, summary,
  parity, determinism, failures, skipped tools, and validation.
- `report.md`: human-readable table.
- `outputs/`: retained tool outputs (large; do not commit). Use
  `--keep-outputs first` to retain one completed output per tool and prune later
  repetitions after hashing.

For each completed trial the report includes wall time, internal/work time, CPU time,
peak RSS, records, output bytes, canonical record hash, output tree hash, versions, and
the verification result when available. Failed, timed-out, OOM, and skipped trials are
preserved with exit codes and stderr tails.

## Summary statistics

Medians, minima, maxima, and IQRs are computed from measured trials only. Warm-ups are
excluded. Raw per-run values remain in `report.json` and `comparison-large.json`. Do not
delete outliers without recording why.

## Validation and aggregation

```sh
python benchmarks/verify_results.py --report benchmarks/runs/<run-id>/report.json --json
python benchmarks/compare.py --reports benchmarks/runs/*/report.json \
  --json-out benchmarks/results/comparison-large.json
```

Validation checks the source commit, environment, fixture manifest, trial evidence,
recomputed summary, determinism, and available parity. Aggregation keeps raw run values and
does not collapse different profiles or hosts into one number.

## Kaggle

`benchmarks/kaggle/` contains a private notebook and kernel metadata. The notebook clones
the benchmark branch, installs optional baselines if needed, runs Tiers A/B and a Tier C
pilot with `--keep-outputs first`, validates every report, and copies compact artifacts to
`/kaggle/working`. Tier C retains one output per tool, not every repetition, to stay
within Kaggle's working-disk budget.

Tier D/E are intentionally not automated. They need a resource plan, a pilot, and a host
with sufficient memory, disk, and session time.

## Guardrails

- Do not claim DATUM is faster without a same-host, same-commit experiment that shows it.
- Do not mix full-build DATUM timings with transform-only competitor timings.
- Do not generalize synthetic fixture results to real customer corpora.
- Do not commit generated datasets, raw outputs, or multi-hundred-megabyte reports.
- Do not overwrite baseline evidence. Add a new run directory.
- Report machine, dependency versions, commit, repetitions, and variability with every
  comparison.
