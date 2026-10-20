# ADR 0003: Manifest plus independent verification

- Status: Accepted
- Date: 2026-10-20

## Context

A build log can claim success while output files are truncated, modified, or inconsistent
with their schema. Provenance is only useful if the published artifact can be checked later,
possibly on another machine.

## Decision

Publish a versioned `manifest.json` containing:

- build ID and output identity;
- source input hashes and statuses;
- configuration and schema fingerprints;
- output paths, sizes, hashes, and record counts;
- sidecar and rejection-log entries;
- quality summary, cache summary, performance, and environment details.

Implement `datum verify` as an independent reader that:

- validates manifest structure and schema version;
- recomputes file hashes, byte sizes, line counts, and schema validity;
- checks sidecar record IDs, output references, and checksums;
- reconciles quality counts and status with the manifest;
- recomputes output identity from written bytes;
- optionally re-hashes sources.

The build runs the verifier against staging before publishing. Built-time counters are never
the sole source of truth for verification.

## Consequences

Positive:

- Tampering and partial writes are detectable.
- Builds are auditable without rerunning extraction.
- Output identity offers a compact equality check.

Negative:

- Verification re-reads all output bytes.
- Manifest bytes are nondeterministic because they contain timestamps and environment data.
- Manifest versions require a compatibility policy.

## Follow-up

Add a build-comparison command and document identity differences. Keep verification
independent of build internals.
