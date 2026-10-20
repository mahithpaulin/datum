# ADR 0001: Python standard library and local-first execution

- Status: Accepted
- Date: 2026-10-20

## Context

DATUM must run on modest local machines, including an Android/Termux phone used for
development. The core build must not require network access, a service account, or a large
dependency tree. Heavy numerical frameworks are unavailable or impractical in the target
environment.

## Decision

Implement the core compiler in Python 3.11+ using the standard library. Keep required runtime
dependencies at zero. Isolate optional capabilities, such as the `pypdf` PDF backend, behind
explicit extras and manifest dependency records.

Use worker threads for I/O-bound extraction and mapping. Keep deterministic compilation in a
single thread. Do not use `ProcessPoolExecutor` as a requirement, because process spawning is
not reliable in the primary development environment.

## Consequences

Positive:

- Simple installation and offline operation.
- Small attack surface for the core.
- Portable to constrained and air-gapped environments.
- Tests run without a service stack.

Negative:

- CPU-bound parsing is limited by the GIL.
- Large corpora may outgrow the single-process compile stage.
- Some format support is necessarily limited compared with dedicated libraries.
- Performance claims must be measured rather than inferred from the implementation.

## Follow-up

Benchmark large-corpus behavior before adding process parallelism. If multiprocessing is
added, preserve deterministic ordering by keeping the final compiler single-writer and by
spooling worker output.
