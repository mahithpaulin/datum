# ADR 0006: AI assistance disabled in the core build

- Status: Accepted
- Date: 2026-10-20

## Context

AI-assisted extraction, labeling, or repair can improve messy corpora, but it introduces
network dependencies, model versioning, cost, latency, privacy exposure, and
nondeterminism. The first release must establish a trustworthy deterministic baseline.

## Decision

Do not wire AI providers into the build in `0.1.0`.

- `ai.enabled = true` is a configuration error.
- `datum/ai/base.py` defines request, response, provider, and error types for future work.
- No secrets, API keys, or network calls are used by the core.
- The manifest records no AI dependency or provider activity because none occurs.

## Consequences

Positive:

- Builds stay local, deterministic, and auditable.
- The quality model measures mechanical transformations only.
- No accidental data exfiltration or per-record cost.

Negative:

- Messy corpora still require external cleaning.
- The provider interface is unused and may evolve.
- Users expecting AI features must wait or preprocess outside DATUM.

## Follow-up

If AI is added, make it an explicit, versioned transformation stage with provider/model/
prompt fingerprints, cost accounting, redaction controls, caching, and a deterministic
fallback. It must never silently replace the baseline build.
