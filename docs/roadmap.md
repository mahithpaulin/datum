# Roadmap

The roadmap is ordered by evidence and risk, not by feature count. Dates are intentionally
absent until pilots establish demand. Each milestone has exit criteria; unfinished criteria
move forward rather than being declared done.

## v0.1.0 — working vertical slice (current)

Implemented:

- Strict TOML configuration with explicit schemas and quality thresholds.
- Deterministic discovery, extraction, mapping, chunking, deduplication, splitting, export.
- Built-in text/completion/instruction/chat/QA/classification schemas and custom schemas.
- TXT/Markdown/code, CSV/TSV, JSON/JSONL, DOCX, and text-PDF adapters.
- Sidecar, inline, both, and disabled provenance modes.
- Content-addressed per-file cache.
- Build manifest, build ID, output identity, quality report, rejection log.
- Independent verifier with tamper detection.
- Atomic publish to `current/`, `previous/`, and `failed/`.
- CLI: build, inspect, validate, verify, manifest, clean, benchmark, version.
- Test suite and synthetic benchmark smoke result.

Exit criteria for treating this as a stable preview:

- [x] End-to-end builds pass verification.
- [x] Tests cover core pipeline, formats, failure paths, and CLI.
- [x] Sample artifacts and documentation exist.
- [ ] At least one real corpus pilot with documented limitations.
- [ ] Two consecutive rebuilds preserve output identity in a user environment.

## v0.2 — trust and operability

Focus: make failures easy to diagnose and the release process safe.

- Structured logging and a concise build event stream.
- Better error locations for CSV, JSON, DOCX, and PDF failures.
- Configurable output retention and safe pruning with dry-run output.
- Manifest comparison command for two builds.
- `verify --sources` coverage on symlinked and moved sources.
- Reproducibility report that explains identity differences.
- Crash-safe cleanup of abandoned staging/spool directories.
- More adversarial input tests and fuzz targets for parsers.
- Documentation for schema migration and cache invalidation.

Exit criteria:

- A failed build always leaves an actionable diagnostic and no partially published output.
- A rebuild audit can explain every output-identity difference.
- Cache corruption is detected and recovered without operator intervention.

## v0.3 — scale and performance

Focus: remove obvious bottlenecks before making performance claims.

- Benchmark on representative corpora and multiple hardware classes.
- Streaming improvements for large text and structured files.
- Bounded-memory compile path with measured peak RSS.
- Optional multi-process extraction where the platform supports it.
- Faster SQLite dedup and configurable batch sizes.
- Index format for very large duplicate sets.
- Backpressure and cancellation behavior under memory pressure.
- Clear performance envelope and failure modes.

Exit criteria:

- Published, reproducible benchmark results with methodology and caveats.
- Peak memory scales predictably with configured limits, not corpus size.
- Cancellation leaves no published partial build.

## v0.4 — format and workflow coverage

Focus: reduce custom scripting for common customer corpora.

Candidate work, gated by pilot demand:

- XLSX and ODS sheet extraction.
- HTML-to-text with explicit boilerplate policy.
- Optional Markdown structure preservation.
- MBOX/EML and common chat export adapters.
- Pluggable adapter entry points with versioned interfaces.
- Dataset filtering by source, language, date, or metadata.
- Stratified splitting with explicit leakage controls.
- PII and secret scanning hooks, with review before any default action.
- Near-duplicate detection as an opt-in, auditable stage.

Exit criteria:

- New adapters pass the same accounting, provenance, and verification contract as core
  adapters.
- No adapter can silently drop records.
- Format support is documented with known extraction limitations.

## v0.5 — commercial hardening

Focus: support paid deployments without overbuilding infrastructure.

- Commercial license and support terms.
- Security review and dependency policy.
- Signed release artifacts and reproducible environment metadata.
- Schema and manifest compatibility guarantees.
- Approval workflow hooks for quality-gated publication.
- Private runner packaging, only if pilots demand scheduled builds.
- Audit reports suitable for customer review.
- Incident response and data-handling documentation.

Exit criteria:

- At least one paid pilot accepted.
- A second customer commits to a repeat engagement or license.
- Security and support boundaries are documented and tested.

## v1.0 — production contract

A 1.0 release requires:

- Stable configuration, schema, manifest, and cache compatibility policy.
- Measured performance envelope on documented hardware.
- Migration tooling and deprecation process.
- Security review and operational runbooks.
- Documented support response targets.
- Evidence from real deployments, not only synthetic fixtures.
- A clear decision on open-source versus proprietary licensing.

## Explicit non-goals for now

- Managed annotation or labeling.
- Model training, evaluation, or inference.
- Web crawling or SaaS connectors.
- Real-time streaming ingestion.
- OCR for scanned documents.
- A hosted multi-tenant platform.
- AI-generated data transformation inside the core build.

## Known gaps to prioritize

1. Real-corpus validation and benchmark evidence.
2. Large-corpus memory and compile-time behavior.
3. Near-duplicate and boilerplate detection.
4. PDF extraction quality and determinism across engines.
5. Format coverage driven by paid customer blockers.
6. PII/licensing/compliance workflows.
7. Operational retention and audit tooling.
