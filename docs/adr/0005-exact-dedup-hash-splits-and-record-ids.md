# ADR 0005: Exact deduplication, hash splits, and content-derived record IDs

- Status: Accepted
- Date: 2026-10-20

## Context

Dataset splits must be stable across worker counts and rebuilds. Deduplication must be
deterministic and explainable. Record identifiers must survive rebuilds when source bytes and
processing configuration are unchanged.

## Decision

- Deduplicate on the canonical JSON serialization of the mapped record after length filtering
  and before splitting. Retain the first occurrence in deterministic compile order.
- Support in-memory and SQLite hash indexes. Do not embed the index in output artifacts.
- Assign splits with `SHA-256(seed + ":" + content_digest)`, mapped into train/validation/test
  ranges. Do not use Python's randomized hash or a stateful random generator.
- Derive record IDs from `SHA-256(path + NUL + source_digest + NUL + ordinal + NUL + chunk_index)`
  truncated to 24 hex characters.

Record IDs are not content hashes of the output record. They identify the source position and
chunk, so two identical records at different positions can be distinguished even though only
one survives deduplication.

## Consequences

Positive:

- Splits and IDs are stable across runs and worker counts.
- Dedup outcomes are reproducible and auditable.
- Changing source bytes or chunking changes identities as expected.

Negative:

- Exact dedup does not remove near-duplicates.
- Changing chunking invalidates IDs and splits.
- The memory index grows with retained records.
- Split assignment is not stratified by label or source.

## Follow-up

Evaluate stratified splitting and near-duplicate detection as opt-in stages. Preserve the
current behavior as the deterministic baseline.
