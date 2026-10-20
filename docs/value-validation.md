# Value validation

This page records what the current evidence supports and what it does not. It is deliberately
conservative: commercial claims must follow measurements, not the reverse.

Recorded on 2026-10-20 from commit `8536550` and GitHub Actions run `39377829817`.

## Verdict

| Claim | Evidence | Status |
| --- | --- | --- |
| Raw transformation speed | CI: DATUM 1.305 s median vs pandas 0.426 s and DuckDB 0.394 s. Local Tier A: DATUM 53.545 s vs Python reference 13.673 s | **Not supported** |
| Lower memory than DataFrame/OLAP engines | CSV peak RSS: DATUM 31.1 MiB vs pandas 91.0 MiB and DuckDB 177.2 MiB. Local 10.5 MB CSV: DATUM 40.8 MB vs Python 111.2 MB | Supported on these fixtures |
| Lower memory than a minimal Python loop | Text peak RSS: DATUM 29.2 MiB vs reference 26.8 MiB | **Not supported on the text fixture** |
| Correct output on the measured workloads | CI: 29,940 CSV and 44 text records. Local Tier A: 98,625 CSV records. Zero missing, extra, or duplicate records | Supported on these fixtures |
| Easier to audit | Manifest, quality report, rejection ledger, independent verifier, tamper detection, deterministic rebuild | Supported by the evidence below |
| More reliable in general | Deterministic rebuilds and quality gates are tested; no real-corpus field data exists | **Not proven** |
| Willingness to pay | No customer interviews, paid pilots, or revenue | **Unproven** |

The defensible commercial position is **auditable, reproducible local dataset builds with
modest memory overhead** — not raw speed. A speed-led sales claim would lose to pandas and
DuckDB on the measured transform.

## Correctness and parity

- Full test suite: **52 passed** (including the large-scale harness tests).
- Both example builds: passed, rebuilt with the same build IDs and output identities.
- Committed snapshots: `examples/expected` and `examples/structured/expected`, **33 verification
  checks each**, all passed.
- Cross-tool parity: **100% record parity** on both benchmark workloads. No tool lost, invented,
  or duplicated records relative to the independent Python reader.
- Pandas produced byte-identical CSV JSONL to DATUM. DuckDB's JSON serialization differed
  byte-for-byte, but the parsed records matched.

## Performance and memory

The checked-in report is `benchmarks/results/comparison-ci.json`, with medians over three runs
on a 4-vCPU, 16 GB GitHub Actions runner (CPython 3.13.16, pandas 3.0.6, DuckDB 1.5.6).

Absolute times are runner-specific. An earlier run of the same code measured 3.115 s for
DATUM, 0.630 s for pandas, and 0.533 s for DuckDB on this workload. The ranking was stable,
but absolute times varied by more than 2x, so use these results for relative ordering and
mechanics, not as a portable performance guarantee.

### CSV transform: 30,000 rows, 2.87 MB input

| Tool | Median wall | Input MiB/s | Records/s | Peak RSS | Parity |
| --- | ---: | ---: | ---: | ---: | --- |
| DATUM (full build) | 1.305 s | 2.097 | 22,934 | 31.1 MiB | 100% |
| pandas | 0.426 s | 6.424 | 70,248 | 91.0 MiB | 100% |
| DuckDB | 0.394 s | 6.946 | 75,951 | 177.2 MiB | 100% |

### Text ingest: 48 files, 0.76 MB input

| Tool | Median wall | Input MiB/s | Records/s | Peak RSS | Parity |
| --- | ---: | ---: | ---: | ---: | --- |
| DATUM (full build) | 0.189 s | 3.845 | 233 | 29.2 MiB | 100% |
| Python reference | 0.116 s | 6.276 | 381 | 26.8 MiB | 100% |

DATUM performs a complete build: schema validation, quality accounting, manifest generation,
and post-build verification. The baselines perform only the stated transform. This makes the
timing comparison conservative for DATUM, but it does not change the conclusion: the current
release is not a faster CSV transformer. The current run shows roughly a 3x gap to pandas and
DuckDB; the earlier run showed a larger gap. Either way, a speed-led claim is unsupported.

The memory result depends on the workload and baseline. DATUM used substantially less memory
than pandas and DuckDB on the CSV fixture and slightly more than the minimal text reference.
Do not generalize either result to larger or different corpora without measuring.

## Auditability demonstration

Inspect the quality and rejection ledger:

```sh
python -m datum.cli quality examples/structured/expected
```

Observed result (repository prefix shortened in the rejected-log path):

```text
Quality: PASS
Build ID: c384ce7368564f71f7547b7aa3dd405f
Exported: 8
Rejected: 4
  duplicate: 1
  schema_invalid: 2
  too_short: 1
Errors:
  schema_invalid: 2
    reviews.jsonl: $.label: value is not one of the allowed enum values
    reviews.jsonl: $.score: value above maximum 5.0
Thresholds: all passed
Invariants: all passed
Rejected log: <repo>/examples/structured/expected/rejected.jsonl
```

Verify the committed bytes:

```sh
python -m datum.cli verify examples/structured/expected
```

This recomputes 33 checks, including output hashes, schema validity, counts, provenance,
quality invariants, split accounting, and output identity.

Tamper with a copy and verify again:

```sh
work="$(mktemp -d)"
cp -a examples/structured/expected "$work/build"
printf '{"question":"untracked","answer":"hand edited","label":"supported"}\n' >> "$work/build/train.jsonl"
python -m datum.cli verify "$work/build"
test $? -eq 1
rm -rf "$work"
```

Observed failures include `output_sha256`, `output_bytes`, `output_record_count`,
`quality_exported_count`, `split_counts`, and `output_identity`. A hand-edited dataset is not
silently accepted.

A matching source hash check is also covered by the acceptance test:

```sh
python -m pytest -q tests/test_customer_acceptance.py
```

That test imports JSONL, inspects format detection, applies a custom schema and field mapping,
builds, reviews three rejection categories, verifies with source hashes, tampers with the
dataset, and confirms verification fails.

## Large-scale benchmark status

The configurable harness is implemented in `benchmarks/run.py`; Tier A and Tier B Kaggle
runs plus a Tier C CSV/JSONL pilot are defined in `benchmarks/kaggle/`. The harness records
raw trials, medians/minima/maxima/IQRs, peak RSS, parity, determinism, and preserved
failures. See [benchmark methodology](benchmark-methodology.md),
[performance analysis](performance-analysis.md), and
[competitive assessment](competitive-assessment.md).

The local Tier A CSV result is already negative for speed: on a 10.5 MB fixture, DATUM's
full build took 53.545 s median versus 13.673 s for the Python transform reference, while
using 40.8 MB versus 111.2 MB peak RSS. This does not support a speed claim. The Kaggle
tier results must be added before any scaling conclusion is made.

## Reproducibility evidence

- Two consecutive text builds produced build ID
  `a45d94a913b96579fe9a1e000c531eae` and output identity
  `a55c1e70f4819a8f21a8a93215504591a6732047c528c406e4c23123f791b9ca`, matching the committed
  snapshot.
- The structured example rebuilt to
  `c384ce7368564f71f7547b7aa3dd405f` with output identity
  `86a1fb65f09b78700b0962786a567f395f11bfb3446fbe5649bd911fc2422c6d`.
- CI passes the full suite on Python 3.11–3.14 and installs the built wheel in a clean
  environment.

## What this proves

Supported: DATUM produces correct output on the measured fixtures, keeps audit evidence that
detects tampering, rebuilds deterministically, and uses less memory than pandas and DuckDB on
the CSV fixture.

Not supported: that DATUM is faster than established tools, that it is more reliable on real
customer corpora, that it improves model quality, or that customers will pay. Those require
real-corpus pilots and commercial validation, described in
[the commercial plan](commercial-plan.md).
