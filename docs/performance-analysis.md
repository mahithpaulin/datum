# Performance analysis

This is an evidence log, not a performance claim. It separates the checked-in CI
comparison from the larger local and Kaggle experiments. Absolute times are not comparable
across hosts.

## Baseline

- Source commit: `52ea9dabbfb8f24af37287fb805b9db6d113fa39`.
- Baseline evidence: `benchmarks/results/baseline-52ea9da/`.
- Checked-in comparison: `benchmarks/results/comparison-ci.json` from source `8536550`,
  GitHub Actions run `39377829817`.
- Aggregated large-scale comparison (generated after the Kaggle runs):
  `benchmarks/results/comparison-large.json`.

## CI comparison (4 vCPU, Python 3.13.16)

CSV transform, 30,000 rows / 2.87 MB:

| Tool | Median wall | Input MiB/s | Peak RSS | Parity |
| --- | ---: | ---: | ---: | ---: |
| DATUM full build | 1.305 s | 2.097 | 31.1 MiB | 100% |
| pandas | 0.426 s | 6.424 | 91.0 MiB | 100% |
| DuckDB | 0.394 s | 6.946 | 177.2 MiB | 100% |

Text ingest, 48 files / 0.76 MB:

| Tool | Median wall | Input MiB/s | Peak RSS | Parity |
| --- | ---: | ---: | ---: | ---: |
| DATUM full build | 0.189 s | 3.845 | 29.2 MiB | 100% |
| Python reference | 0.116 s | 6.276 | 26.8 MiB | 100% |

The earlier workflow run measured DATUM at 3.115 s, pandas at 0.630 s, and DuckDB at
0.533 s on the same CSV workload. Absolute values moved by more than 2x while the ranking
stayed the same. Treat these as runner-specific.

## Local Tier A CSV run (Termux, Python 3.14.6)

Run `tierA-csv-10`, three measured repetitions, one warm-up, DATUM plus the Python
reference. Compact evidence: `benchmarks/results/local-tier-a/before/`. Fixture: 10,511,219 input bytes, 102,144 CSV rows, 98,625 exported records.
The fixture hash and all trial evidence are in the run report.

| Mode / tool | Median wall | Min | Max | IQR | Peak RSS | Records |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| equivalent / DATUM | 53.545 s | 53.541 s | 54.134 s | 0.296 s | 40.8 MB | 98,625 |
| equivalent / Python reference | 13.673 s | 13.671 s | 13.680 s | 0.004 s | 111.2 MB | 98,625 |
| workflow / DATUM | 66.367 s | 66.292 s | 66.466 s | 0.087 s | 44.0 MB | 98,625 |

Record-level parity was 100%: zero missing, zero extra, zero duplicates. DATUM's canonical
output hash and record count were stable across all repetitions.

DATUM internal stage breakdown for the first equivalent trial:

| Stage | Seconds |
| --- | ---: |
| Compile | 21.119 |
| Dedup hashing | 4.519 |
| Dedup index | 0.259 |
| Processing total | 27.204 |
| Verification | 3.574 |
| Internal total | 52.085 |
| Wall total | 53.545 |

This is a phone. The Python reference was about 3.9x faster in wall time; DATUM used about
2.7x less peak memory. The workflow added about 24% wall time for provenance sidecars,
splitting, cache configuration, and verification.

### Local optimization trial (after baseline)

One safe normalization optimization was implemented after the baseline: control-character
removal now uses a compiled Unicode `Cc` range substitution instead of a per-character
`unicodedata.category` loop, and normalization-stat accumulation avoids rebuilding a
dataclass dictionary for every record. The replacement has an explicit equivalence test for
`Cc` handling and preserves the same counters.

Same host, same 10,511,219-byte fixture. The baseline used three measured repetitions and
one warm-up; the optimized run used five measured repetitions and no warm-up. Compact
evidence: `benchmarks/results/local-tier-a/after/`.

| Mode / tool | Baseline median | Optimized median | Change | Peak RSS | Parity |
| --- | ---: | ---: | ---: | ---: | ---: |
| equivalent / DATUM | 53.545 s | 35.617 s | -33.5% | 42.3 MB | 100% |
| workflow / DATUM | 66.367 s | 46.123 s | -30.5% | 43.9 MB | stable |
| equivalent / Python reference | 13.673 s | 10.472 s | reference variance | 112.4 MB | 100% |

The canonical output hash (`ea642650...`) and 98,625 exported records were identical before
and after optimization. Internal stages moved from compile 21.119 s to 16.652 s, processing
27.204 s to 15.335 s, dedup hashing 4.519 s to 3.555 s, and verification 3.574 s to 2.704 s.
The optimized DATUM build is still about 3.4x slower than the Python reference in the
optimized run. This is a local before/after result, not yet replicated at Tier B/C or on
Kaggle.

The stage breakdown identifies the primary local bottleneck: compilation (schema
validation, normalization, per-record serialization, and output writing), followed by
deduplication hashing. Verification is measurable but not dominant at this size. This is a
profile target, not evidence that an optimization is safe.

## Interpretation

- The speed result is negative for DATUM on the measured transforms. The current evidence
  does not support a raw-speed claim against pandas, DuckDB, Polars, or a minimal Python
  reader.
- DATUM's lower memory on the CSV fixture is consistent with streaming ingestion. It is
  workload-specific and must not be generalized to all formats or all scales.
- The workflow overhead is real and must be reported separately. It buys provenance,
  quality accounting, deterministic identity, and independent verification.
- The local absolute timings are not portable to Kaggle or to a server. Only relative
  comparisons from the same run should be used.
- Cross-run variance on shared runners is large enough that medians/min/max/IQR and raw
  trials are mandatory.

## Pending evidence

The Kaggle notebook in `benchmarks/kaggle/` runs Tiers A/B across profiles and a Tier C CSV
and JSONL pilot. When those reports are downloaded, this page must be updated with:

- profile-by-profile time, memory, parity, and determinism;
- whether DATUM's relative position changes with scale;
- which stage dominates at 100 MiB and 1 GiB;
- resource failures or skipped tiers;
- replication of the local normalization optimization at larger tiers.

Until then, no scaling conclusion should be drawn from Tier A alone.
