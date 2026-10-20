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
ranking, and wall times include process startup for every tool.

The script checks output content equality across tools and fails if they disagree. DATUM's
additional work is recorded in every report so a reader cannot mistake the comparison for an
isolated transform speed test.

Run it from the repository root:

```sh
python benchmarks/compare.py \
  --rows 30000 \
  --text-files 48 \
  --text-kib 16 \
  --workers 2 \
  --json-out benchmarks/results/comparison.json
```

`pandas` and `duckdb` are optional; missing tools are listed as skipped. The
`.github/workflows/benchmark.yml` workflow installs both and publishes the comparison report
as a downloadable artifact. No cross-tool result is checked into the repository yet.

## Interpreting results

- Compare runs produced by the same commit, fixture settings, host, and power state.
- Use `build_seconds` for pipeline comparisons and wall time for end-to-end user experience.
- Treat peak RSS as a coarse process-level measurement from the platform.
- Do not use the synthetic result as evidence about model quality, corpus diversity, dedup
  effectiveness, or real-world ingestion speed.
- A cache result is invalid if the cache was populated by a different pipeline, schema,
  adapter, or source digest. The cache key is designed to prevent that reuse.

## Missing evidence

There is no public acceptance corpus, no published cross-tool result, no cold-storage
benchmark, and no long-running large-corpus result yet. The comparison harness exists, but its
output must be reviewed against the stated fairness limits. These are roadmap items and must
be produced before making public performance claims.
