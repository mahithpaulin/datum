# Value validation

This page records what the current evidence supports and what it does not. It is deliberately
conservative: commercial claims must follow measurements, not the reverse.

Recorded on 2026-10-20 from commit `e631100` and GitHub Actions run `39377137608`.

## Verdict

| Claim | Evidence | Status |
| --- | --- | --- |
| Raw transformation speed | CSV: DATUM 3.115 s median vs pandas 0.630 s and DuckDB 0.533 s. Text: DATUM 0.334 s vs Python reference 0.188 s | **Not supported** |
| Lower memory than DataFrame/OLAP engines | CSV peak RSS: DATUM 31.1 MiB vs pandas 91.2 MiB and DuckDB 175.1 MiB | Supported on this fixture |
| Lower memory than a minimal Python loop | Text peak RSS: DATUM 29.1 MiB vs reference 26.9 MiB | **Not supported** |
| Correct output on the measured workloads | 29,940 CSV records and 44 text records; zero missing, extra, or duplicate records for every tool | Supported on these fixtures |
| Easier to audit | Manifest, quality report, rejection ledger, independent verifier, tamper detection, deterministic rebuild | Supported by the evidence below |
| More reliable in general | Deterministic rebuilds and quality gates are tested; no real-corpus field data exists | **Not proven** |
| Willingness to pay | No customer interviews, paid pilots, or revenue | **Unproven** |

The defensible commercial position is **auditable, reproducible local dataset builds with
modest memory overhead** — not raw speed. A speed-led sales claim would lose to pandas and
DuckDB on the measured transform.

## Correctness and parity

- Full test suite: **47 passed**.
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

### CSV transform: 30,000 rows, 2.87 MB input

| Tool | Median wall | Input MiB/s | Records/s | Peak RSS | Parity |
| --- | ---: | ---: | ---: | ---: | --- |
| DATUM (full build) | 3.115 s | 0.879 | 9,611 | 31.1 MiB | 100% |
| pandas | 0.630 s | 4.348 | 47,543 | 91.2 MiB | 100% |
| DuckDB | 0.533 s | 5.133 | 56,124 | 175.1 MiB | 100% |

### Text ingest: 48 files, 0.76 MB input

| Tool | Median wall | Input MiB/s | Records/s | Peak RSS | Parity |
| --- | ---: | ---: | ---: | ---: | --- |
| DATUM (full build) | 0.334 s | 2.171 | 132 | 29.1 MiB | 100% |
| Python reference | 0.188 s | 3.859 | 234 | 26.9 MiB | 100% |

DATUM performs a complete build: schema validation, quality accounting, manifest generation,
and post-build verification. The baselines perform only the stated transform. This makes the
timing comparison conservative for DATUM, but it does not change the conclusion: the current
release is not a faster CSV transformer.

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
