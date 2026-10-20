# Benchmark methodology

This document defines the large-scale competitive benchmark. It is intentionally
adversarial toward the hypothesis that DATUM is fast: the benchmark must be capable of
showing that DATUM loses, and it must preserve the evidence when it does.

## Questions

1. How do DATUM and established tools compare on controlled workloads as input grows?
2. Which parts of DATUM's pipeline dominate time and memory?
3. Does DATUM preserve record-level output while doing its additional validation,
   provenance, quality, and verification work?
4. Where does DATUM's auditability and memory profile help, and where does it not?
5. Which optimizations improve measured bottlenecks without weakening correctness or
   deterministic output?

The default hypothesis is **no advantage**. A result is reported as a finding, not as a
marketing premise.

## Baselines

| Tool | Comparison role |
| --- | --- |
| DATUM | Complete build, including validation, manifest, quality, and verification |
| Python reference | Minimal independent implementation of the declared transform |
| pandas | DataFrame transform baseline |
| DuckDB | In-process analytical SQL baseline |
| Polars | DataFrame/query baseline |
| Hugging Face Datasets | Optional workflow-level context, not a drop-in equivalent |

Competitor code lives in `benchmarks/workloads.py`. It is deliberately separate from
`datum` so the reference does not call DATUM internals.

## Workloads

### many-small-text

Read all `.txt` files recursively, decode UTF-8 with replacement, normalize line endings
and control characters, strip outer whitespace, drop empty records, deduplicate exact text,
and write canonical JSONL `{"text": ...}`.

### large-text

Read large text documents and apply DATUM's configured character chunking with overlap.
This is a DATUM workflow workload, not an equivalent transform; no competitor chunker is
claimed to be semantically identical.

### jsonl-training

Read well-formed newline-delimited JSON records with `prompt`, `completion`, and `label`.
Ignore blank lines and reject null/non-string fields. Require a valid label, minimum total
content length 5, and maximum total content length 12,000. Normalize strings, drop exact
duplicates, and write canonical JSONL with all three fields.

### csv-transform

Read CSV with `prompt` and `completion` columns. Normalize line endings and outer
whitespace, drop empty pairs, deduplicate exact pairs, and write canonical JSONL.

### mixed-formats

Read text, Markdown, CSV, TSV, JSON, JSONL, DOCX, and PDF using the declared completion
separator. Unsupported files are skipped with accounting. This workload checks adapter
coverage and rejection behavior, not a portable competitor ranking.

### adversarial-quality

Read malformed JSONL, invalid schema records, overlong records, corrupt DOCX/PDF/JSON,
unsupported binaries, and filenames with spaces, Unicode, and leading dashes. The point is
accounting, failure classification, and preservation of bad input evidence.

Equivalent profiles use syntactically readable inputs so every baseline sees the same source
records. Invalid UTF-8 and syntactically corrupt files are isolated in the DATUM-only
`adversarial-quality` profile, where parser recovery semantics are not portable.

## Fixtures and tiers

`benchmarks/generate_data.py` creates seeded fixtures and records:

- profile and tier;
- target and actual input bytes;
- file and row counts;
- duplicate, empty, malformed, missing, schema-invalid, and overlong counts;
- per-profile parameters and seed;
- config paths;
- optional SHA-256 over the complete input tree.

Tiers are approximate input-byte targets:

| Tier | Target |
| --- | ---: |
| A | 10 MiB |
| B | 100 MiB |
| C | 1 GiB |
| D | 5 GiB |
| E | 10 GiB |

A tier is not attempted merely because it is listed. The controller checks available memory
and disk and refuses to start unless `--allow-low-resource` is supplied. The Kaggle
notebook runs A and B broadly and C as a CSV/JSONL pilot. D/E require a separate plan.

## Experimental design

- One warm-up per mode/tool before measured trials when practical.
- Five measured repetitions by default; three for Tier C pilots and local smoke runs.
- Tool order rotates by repetition so one tool does not always run first.
- Tools run sequentially, never concurrently.
- The fixture remains fixed for a run.
- Cold/warm cache state is explicit. The controller passes `--no-cache` for DATUM
  equivalent runs; workflow cache configuration is recorded in the fixture config.
- A failed competitor does not abort the whole experiment. A failed DATUM run does make
  the report `ok: false`.
- Resume skips completed trials and retries incomplete ones.
- Outliers are preserved. They may be discussed, but not silently deleted.

## Metrics

### Performance

- Wall time from process start to exit for every trial.
- DATUM internal total, processing, compile, dedup hashing/index, and verification times.
- Input and output bytes; input MiB/s; records/s.
- Median, minimum, maximum, and IQR across measured trials; raw values retained.
- Cold/warm cache timing where configured.
- Startup probe time for each tool (`tool_startup_seconds`).

### Resources

- Peak RSS from the tool report or a `/proc`/`psutil` monitor.
- CPU time for the child process.
- Output and cache size.
- Available memory and disk at run start.
- Timeouts and OOM classifications.

### Correctness

- Record count and canonical record hash per trial.
- Record-level set parity against the Python reference where available.
- Missing, extra, and duplicate record counts.
- DATUM report count versus actual output count.
- Rejection accounting from the build report.
- Independent `datum verify --sources` result when enabled.
- Determinism across repetitions: stable record count and stable canonical hash.

### Reliability

- Failed, timed-out, OOM, and skipped statuses are preserved.
- The report distinguishes unsupported tools from failures.
- Fixture hash and source commit make evidence traceable.
- Report validation recomputes the summary and checks evidence shape.

## Threats to validity

- **Host variance.** A phone or a shared Kaggle VM is not a controlled benchmark host.
  Compare same-host, same-commit runs. Absolute times are not portable.
- **Python version.** CPython 3.14 on Termux and CPython 3.11/3.13 on Kaggle behave
  differently.
- **Warm cache.** The fixture may be in the OS page cache even when the application cache is
  disabled. Warm-ups and repeated trials make this explicit.
- **Different work.** DATUM's full build does more than competitor transforms. The report
  separates modes but the overhead remains real.
- **Synthetic data.** Generated records are not customer data. They exercise mechanics,
  not domain quality or dedup effectiveness on real corpora.
- **Single-machine runs.** No claim of universal scaling is made without multiple hosts.
- **Compression.** DOCX/PDF sizes are compressed and do not follow the nominal share of the
  mixed fixture.
- **Tool versions.** pandas, DuckDB, and Polars behavior changes across releases; versions
  are recorded.

## Profiling and optimization

Profile before optimizing. Candidate areas from code inspection are:

- repeated hashing and verification reads;
- whole-file reads in the text adapter;
- sorting all record lengths in `datum/quality.py`;
- canonical JSON serialization per record;
- memory dedup index growth;
- worker-thread limits under the GIL.

Any optimization must be evaluated with a before/after run on the same host and profile.
Correctness, deterministic output, provenance, rejection accounting, and quality gates must
remain intact. If no safe optimization is found, that is a valid documented outcome.

## Reporting

Every report states:

- source commit and dirty state;
- machine, CPU count, memory, disk, Python, and dependency versions;
- fixture profile, tier, target/actual bytes, seed, and hash;
- modes, tools, repetitions, warm-ups, and settings;
- raw trials, summary, parity, determinism, failures, and skipped tools;
- limitations and the exact workloads.

The final assessment uses tables and explicitly labels unsupported claims. It must not
convert a mechanics comparison into a general speed or product-superiority claim.

## Acceptance mapping

- Baseline preserved: `benchmarks/results/baseline-52ea9da/`.
- Reproducible harness: seeded generator, configs, reports, and resume.
- Multiple tiers: A/B plus C pilot on Kaggle, with D/E documented as not attempted.
- Traceable trials: source commit, fixture hash, raw `trials.jsonl`.
- Correctness: record parity and deterministic hashes.
- Workload differences: equivalent and workflow modes are separate.
- Failures visible: failed/skipped/unsupported status is retained.
- Optimization: before/after measurement or a documented reason none was safe.
- Tests: harness tests plus the existing suite.
- Claims: no unsupported superiority statement.
