# Competitive assessment

**Verdict: DATUM is not currently a faster data-processing product than pandas, DuckDB,
Polars, or a minimal Python reader. Its defensible differentiator is auditable,
deterministic, local dataset builds with explicit rejection accounting and verification.**

This assessment follows the measurements. It is not a claim that DATUM has no value; it
identifies where that value is and is not supported.

## Evidence used

- Checked-in CI comparison: 30,000-row CSV and 48-file text fixture, source `8536550`,
  GitHub Actions run `39377829817`.
- Local Tier A CSV run `tierA-csv-10`: 10.5 MB input, 98,625 records, DATUM plus Python
  reference, three repetitions. A later normalization optimization reduced DATUM from
  53.545 s to 35.617 s on the same fixture, with identical output.
- Baseline evidence from `52ea9da` in `benchmarks/results/baseline-52ea9da/`.
- Full test suite and acceptance workflow.
- Large-scale Kaggle results are appended after the notebook completes; this page must not
  imply that those runs already happened.

## Competitor map

| Tool | Core strength | Where it beats DATUM | Where DATUM differs |
| --- | --- | --- | --- |
| pandas | Familiar DataFrame API, broad ecosystem | Faster CSV transform on the CI fixture; richer analysis | DATUM has smaller peak RSS on CSV, explicit rejection ledger, manifest, verifier |
| DuckDB | Fast analytical SQL, low setup, strong CSV/Parquet performance | Faster CSV transform; larger memory on the measured fixture | DATUM is not an analytical query engine; it is a build-and-evidence pipeline |
| Polars | Fast columnar execution, lazy queries | Expected to be faster on tabular transforms; measured values pending Kaggle | DATUM focuses on ingestion accountability and deterministic artifact identity |
| Minimal Python reference | Transparent, no dependencies, easy to audit | Faster text and CSV transforms on the measured fixtures | DATUM adds schema validation, quality accounting, provenance, and verification |
| Hugging Face Datasets | Dataset distribution, caching, hub integration, ecosystem | Ecosystem reach, remote datasets, model-oriented workflows | DATUM is local/offline, contract-first, and produces independent audit evidence |

## What the evidence supports

### Supported

- **Correct output on measured fixtures.** Record-level parity was 100% on the CI CSV/text
  workloads and the local Tier A CSV run. No missing, extra, or duplicate records.
- **Lower peak memory than pandas and DuckDB on the CI CSV fixture.** DATUM used 31.1 MiB
  versus 91.0 MiB and 177.2 MiB. This is one fixture, not a general law.
- **Lower peak memory than the Python reference on the local 10.5 MB CSV run.** 40.8 MB
  versus 111.2 MB.
- **Deterministic output.** Repeated DATUM builds produced stable canonical hashes and
  record counts in the observed runs.
- **Auditability.** Manifest, resolved config, quality report, rejection ledger, source
  hashes, independent verifier, and tamper detection are implemented and tested.

### Not supported

- **DATUM is faster.** CI: pandas 0.426 s and DuckDB 0.394 s versus DATUM 1.305 s on CSV.
  Local Tier A: Python reference 10.472 s versus optimized DATUM 35.617 s. No speed claim
  survives this evidence.
- **DATUM is more memory-efficient in general.** The text fixture showed DATUM slightly
  above the Python reference (29.2 vs 26.8 MiB). Large-scale memory behavior is pending.
- **DATUM is more reliable on real corpora.** No customer corpus, field incident data, or
  independent replication exists.
- **DATUM improves model quality or training outcomes.** Not measured.
- **Customers will pay.** No interviews, paid pilots, or revenue evidence.

## Decision guide

Use DATUM when all of the following matter more than raw transform speed:

- You need a deterministic local build with a manifest and resolved configuration.
- You need explicit accepted/rejected/duplicate accounting.
- You need independent post-build verification and source-hash evidence.
- You need a small runtime dependency footprint and offline operation.
- Your workload fits the supported adapters and the full-build overhead is acceptable.

Do not choose DATUM primarily for:

- Maximum CSV/JSONL throughput.
- Analytical SQL or DataFrame exploration.
- Distributed or cluster-scale processing.
- Cloud/hub dataset distribution.
- A proven production record on arbitrary customer data.

## Positioning

The honest position is:

> DATUM builds datasets that can be audited and reproduced, with explicit rejection
> accounting and independent verification, while keeping memory modest on streaming
> workloads. It is not the fastest transformer, and it does not claim to be.

This position is narrower than “better than pandas/DuckDB,” but it is defensible with the
current evidence. A speed-led pitch would lose immediately on the checked-in numbers.

## What would change the assessment

- A consistent DATUM win on a realistic, large, mixed-format workload at equal work.
- A materially lower memory profile that persists at 1–10 GiB.
- A safe optimization that closes the transform gap without weakening evidence.
- Independent users reproducing the audit workflow on real corpora.
- Evidence that audit/rejection/provenance failures cost customers enough to pay for a
  dedicated build tool.

## Commercial implications

The product should be sold as an **auditable dataset build layer**, not as a faster
pandas/DuckDB replacement. A useful commercial experiment would compare the cost of a
failed or unauditable build against DATUM's overhead, not compare MiB/s alone. That
experiment has not been run.
