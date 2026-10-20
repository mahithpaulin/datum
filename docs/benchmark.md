# Benchmark method and smoke result

## What the benchmark measures

The synthetic benchmark measures DATUM's build mechanics on a generated text corpus. It is a
reproducibility and regression signal, not a claim about performance on real corpora, other
hardware, or other tools.

The runner makes three subprocess builds of the same fixture:

1. `cold-no-cache` — empty cache, cache disabled with `--no-cache`.
2. `cold-cache` — empty cache, cache enabled.
3. `warm-cache` — same cache, all files expected to hit.

It checks that all runs succeed and that the dataset output identity is identical. Wall time
includes Python process startup; `build_seconds` is measured inside the build. Throughput
values based on input bytes use wall time and should be read as conservative.

Fixture generation is seeded and deterministic. The benchmark work directory is temporary
unless `--workdir` is supplied. Reports record the environment, DATUM version, fixture
parameters, per-run cache statistics, timings, output size, and peak RSS.

## Smoke result

Recorded on 2026-10-20 from this repository.

Environment:

| Property | Value |
| --- | --- |
| Platform | Android 13, aarch64 |
| Python | CPython 3.14.6 |
| CPU count | 8 |
| Memory | 3.92 GB |
| DATUM | 0.1.0 |

Fixture:

| Property | Value |
| --- | --- |
| Files | 8 text documents |
| Size | 16 KiB target per file |
| Input bytes | 133,104 |
| Workers | 2 |
| Chunking | 8,000 characters, 400 overlap |
| Deduplication | exact, memory index |
| Splits | disabled |
| Provenance | sidecar |

Results:

| Run | Wall s | Build s | Cache hits | Cache misses | Cache writes | Input MiB/s | Peak RSS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| cold-no-cache | 1.118 | 0.482 | 0 | 0 | 0 | 0.114 | 25.0 MiB |
| cold-cache | 1.202 | 0.422 | 0 | 8 | 8 | 0.106 | 25.4 MiB |
| warm-cache | 1.046 | 0.272 | 8 | 0 | 0 | 0.121 | 25.1 MiB |

All three runs exported 24 records, produced 140,568 output bytes, and shared output identity
`97427736c8fa863e0ce91279d998bd94b9b13b14dade9a40efe7637c137c4179`.
The first and second runs necessarily have different cache statistics and timestamps; these
do not enter the output identity. Deterministic output check: **passed**.

On this small fixture, the warm cache reduced in-build time from 0.422 s (cold cache) to
0.272 s. That is a mechanics observation, not a general speedup claim: process startup,
fixture size, filesystem state, and this phone's power state dominate the wall times.

## Reproduce

From the repository root:

```sh
python -m datum.cli benchmark \
  --files 8 \
  --file-kib 16 \
  --workers 2 \
  --json-out benchmarks/results/smoke.json
```

For a larger mechanics run, increase `--files` or `--file-kib`. Keep work on a machine with
enough free disk. Use `--workdir` to retain the generated fixture and build outputs for
inspection:

```sh
python -m datum.cli benchmark --files 32 --file-kib 64 --workers 4 --workdir /path/to/bench
```

The JSON report is versioned by `benchmark_schema_version` (currently `1`).

## Cross-tool comparison

The synthetic benchmark above measures DATUM against itself. `benchmarks/compare.py` adds a
separate mechanics comparison on two narrow workloads:

- **text-ingest** — DATUM against a small independent Python reference that reads sorted text
  files, normalizes, drops empty and duplicate text, and writes JSONL.
- **csv-transform** — DATUM against pandas and DuckDB, when installed. Each tool reads the
  same CSV, trims prompt/completion fields, drops empty and duplicate pairs, and writes JSONL.

DATUM performs a complete build: schema validation, quality accounting, manifest generation,
and post-build verification. The baseline tools perform only the stated transform. For that
reason the comparison is labelled a mechanics comparison, not an apples-to-apples product
ranking, and median wall times include process startup for every tool.

The script runs each tool three times, reports median wall time, and computes input/output
MiB/s, records/s, peak RSS, and record-level parity. Accuracy here means output parity against
an independent Python reader, not model accuracy. It fails if any tool loses, invents, or
duplicates records relative to that reference. DATUM's additional work is recorded in every
report so a reader cannot mistake the comparison for an isolated transform speed test.

Run it from the repository root:

```sh
python benchmarks/compare.py \
  --rows 30000 \
  --text-files 48 \
  --text-kib 16 \
  --workers 2 \
  --repetitions 3 \
  --json-out benchmarks/results/comparison.json
```

`pandas` and `duckdb` are optional; missing tools are listed as skipped. The
`.github/workflows/benchmark.yml` workflow installs both and publishes the comparison report
as a downloadable artifact.

## CI comparison result

Recorded from commit `8536550` by GitHub Actions run `39377829817` on 2026-10-20. The
checked-in report is `benchmarks/results/comparison-ci.json` (`comparison_schema_version` 2).

Environment:

| Property | Value |
| --- | --- |
| Runner | GitHub Actions Ubuntu, 4 vCPU |
| Memory | 16 GB |
| Python | CPython 3.13.16 |
| pandas | 3.0.6 |
| DuckDB | 1.5.6 |
| Repetitions | 3, medians reported |

Fixture: 48 text files at 16 KiB each (760,621 input bytes, 44 output records) and 30,000 CSV
rows (2,870,976 input bytes, 29,940 output records).

CSV transform:

| Tool | Median wall | Input MiB/s | Records/s | Peak RSS | Parity |
| --- | ---: | ---: | ---: | ---: | ---: |
| DATUM (full build) | 1.305 s | 2.097 | 22,934 | 31.1 MiB | 100% |
| pandas | 0.426 s | 6.424 | 70,248 | 91.0 MiB | 100% |
| DuckDB | 0.394 s | 6.946 | 75,951 | 177.2 MiB | 100% |

Text ingest:

| Tool | Median wall | Input MiB/s | Records/s | Peak RSS | Parity |
| --- | ---: | ---: | ---: | ---: | ---: |
| DATUM (full build) | 0.189 s | 3.845 | 233 | 29.2 MiB | 100% |
| Python reference | 0.116 s | 6.276 | 381 | 26.8 MiB | 100% |

All tools produced zero missing, extra, or duplicate records. Pandas was byte-identical to
DATUM on the CSV output; DuckDB's JSON serialization differed byte-for-byte while the parsed
records matched.

These results do **not** support a speed claim: pandas and DuckDB transform the CSV about
three times faster than DATUM's full build, and the Python reference is faster on text. DATUM
used substantially less peak memory than pandas and DuckDB on the CSV fixture, but slightly
more than the text reference. See [value validation](value-validation.md) for the
claim-by-claim assessment.

Absolute times vary between runners. An earlier workflow run of the same code measured
3.115 s for DATUM, 0.630 s for pandas, and 0.533 s for DuckDB on the CSV workload. The
ranking was unchanged, but the absolute values moved by more than 2x. Treat these numbers as
runner-specific and compare runs from the same host and commit.

## Large-scale harness

The CI smoke benchmark and cross-tool comparison are intentionally small. The configurable
large-scale harness is documented in [benchmarks/README.md](../benchmarks/README.md) and
[benchmark methodology](benchmark-methodology.md). It adds seeded fixtures for six profiles,
Tiers A–E, multiple baselines, raw trial evidence, resume support, failure preservation,
record parity, determinism, and independent report validation.

Run a local Tier A experiment with:

```sh
python benchmarks/run.py \
  --profile csv-transform \
  --tier A \
  --mode both \
  --tools python \
  --repetitions 3 \
  --warmups 1
```

The Kaggle notebook in `benchmarks/kaggle/` runs Tiers A/B across profiles and a Tier C
CSV/JSONL pilot. Tier D/E are documented but not automated; they require a separate resource
plan.

## Interpreting results

- Compare runs produced by the same commit, fixture settings, host, and power state.
- Use `build_seconds` for pipeline comparisons and median wall time for end-to-end user
  experience. Report the fixture, hardware, dependency versions, and repetitions with any
  comparison.
- Read throughput and peak RSS together with the parity table; speed without correctness is
  not a useful result.
- Treat peak RSS as a coarse process-level measurement from the platform.
- Do not use the synthetic result as evidence about model quality, corpus diversity, dedup
  effectiveness, or real-world ingestion speed.
- A cache result is invalid if the cache was populated by a different pipeline, schema,
  adapter, or source digest. The cache key is designed to prevent that reuse.

## Missing evidence

There is no public acceptance corpus, no independent replication of the comparison, no
cold-storage benchmark, and no completed multi-gigabyte result yet. The large-scale harness
and Kaggle notebook are implemented; their reports must be added before making scaling
claims. One synthetic CI
comparison is checked in, but it must be read against the stated fairness limits and is not
customer evidence. These are roadmap items and must be produced before making public
performance claims.
