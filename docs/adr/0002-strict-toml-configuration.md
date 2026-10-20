# ADR 0002: Strict TOML configuration with explicit fingerprints

- Status: Accepted
- Date: 2026-10-20

## Context

Dataset builds fail silently when configuration is ambiguous, misspelled, or depends on
implicit defaults. The build also needs a stable identity for caching and reproducibility.

## Decision

Use a single TOML configuration file with strict validation:

- Unknown keys are errors.
- Wrong types and out-of-range values are errors.
- Paths resolve relative to the configuration file.
- Unsupported features, including AI in `0.1.0`, are rejected rather than ignored.
- Derived configuration can be overridden by CLI flags for a single build.

Compute two fingerprints:

- `config_fingerprint` over processing-relevant configuration.
- `pipeline_fingerprint` over the narrower set that affects extraction and mapping.

Exclude runtime-only settings such as worker count, progress output, timestamps, and
environment details from identity decisions.

## Consequences

Positive:

- Misconfiguration is caught before reading the corpus.
- Cache keys and build IDs are stable across equivalent runs.
- Resolved configuration is written to the build for inspection.

Negative:

- Adding a key requires updating validation and documentation.
- Users cannot carry unrelated experimentation keys in the file.
- Strict rejection of AI settings may surprise users who assume a no-op flag.

## Follow-up

Introduce a configuration migration mechanism before changing `config_version`. Never
silently reinterpret an existing key.
