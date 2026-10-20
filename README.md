# DATUM

DATUM is a local data compiler. It turns mixed raw files into validated, reproducible,
AI-ready JSONL datasets, together with a quality report and a build manifest that can be
checked independently.

**Status:** `0.1.0` development preview. The core pipeline is implemented and tested, but
the project is not yet production-ready. AI-assisted processing is intentionally disabled
in this release.

## Why DATUM exists

Raw data preparation is usually hidden inside notebooks and one-off scripts. That makes it
hard to answer basic questions:

- Which source bytes produced this dataset?
- What was removed, why, and how much?
- Can the same configuration produce byte-identical output later?
- Does the exported data actually satisfy the declared schema?

DATUM makes those questions part of the build. It compiles a source directory into a
versioned output directory with explicit accounting, content hashes, deterministic ordering,
and a verifier that does not trust the build process.

## What it does

1. **Discovers** files under a source directory in a stable path order.
2. **Detects** formats from extension, magic bytes, and content without executing input.
3. **Extracts** text or structured records, with limits on size and archive expansion.
4. **Normalizes** line endings, whitespace, and control characters.
5. **Maps** records into a built-in or custom schema.
6. **Chunks** long text when configured.
7. **Filters** empty, short, long, schema-invalid, and duplicate records.
8. **Splits** deterministically using content hashes when splitting is enabled.
9. **Exports** canonical JSONL plus provenance and a rejection log.
10. **Checks quality** invariants and thresholds.
11. **Verifies** output hashes, schema compliance, counts, provenance, and identity.
12. **Publishes** atomically, preserving the previous successful build on failure.

## Quick start

Requires Python 3.11 or newer. DATUM has no required runtime dependencies and runs directly
from a checkout:

```sh
python -m datum.cli build --config examples/datum.toml
python -m datum.cli verify examples/output/example/current --sources
python -m datum.cli manifest examples/output/example/current
```

The text example reads three documents, skips one unsupported image, and exports three text
records. A structured example maps nested JSONL into a custom schema:

```sh
python -m datum.cli build --config examples/structured/datum.toml
python -m datum.cli verify examples/structured/output/structured/current --sources
```

Committed snapshots are available under `examples/expected/` and
`examples/structured/expected/`; verify them without the original source paths:

```sh
python -m datum.cli verify examples/expected
python -m datum.cli verify examples/structured/expected
```

To install the `datum` console script, use `python -m pip install -e .`. The build backend
requires `setuptools >= 68`; offline environments without it should use `python -m datum.cli`
from the repository root instead. See the [quickstart and customer workflow](docs/quickstart.md)
for the full validate → inspect → build → review → verify path.

Useful commands:

| Command | Purpose |
| --- | --- |
| `datum validate` | Validate the configuration, schema, paths, and tokenizer without building |
| `datum inspect` | Show discovered files, detected formats, and unsupported inputs |
| `datum build` | Run a build and publish a verified dataset |
| `datum verify` | Independently verify a build directory or manifest |
| `datum quality` | Show quality status, error counts, thresholds, and invariants |
| `datum manifest` | Show a build manifest summary |
| `datum clean` | Remove current, previous, failed, cache, or staging output |
| `datum benchmark` | Run a reproducible synthetic mechanics benchmark |
| `datum version` | Show version and schema versions |

All commands accept `--json` where a machine-readable result is useful. Use
`python -m datum.cli <command> --help` for the full argument list.

## Output contract

A successful build directory contains:

```text
manifest.json         build identity, inputs, outputs, config, hashes, environment
quality.json          structured counters, invariants, thresholds, and rejection summary
quality.txt           human-readable quality report
config.resolved.json  fully resolved configuration
dataset.jsonl         single split when splitting is disabled
train.jsonl           present when splitting is enabled
validation.jsonl
test.jsonl
provenance.jsonl      sidecar provenance unless mode is inline/both/off
rejected.jsonl        one JSON object per rejected candidate
```

A rejected record is never silently dropped. `rejected.jsonl` and the quality report
reconcile every extracted source record, every candidate chunk, every duplicate, and every
malformed input record.

## Supported inputs

- Plain text, logs, reStructuredText, TeX, Markdown, and many source-code/config extensions
- CSV and TSV with header and delimiter detection
- JSON arrays/objects and JSONL/NDJSON
- DOCX body paragraphs and tables, using the standard library
- Text-based PDFs with a built-in extractor; optional `pypdf` engine if installed

Unsupported formats are reported and skipped by default. Images, audio, video, HTML, XLSX,
Excel, Parquet, and archives are not parsed in this release. See
[limitations](docs/limitations.md).

## Design principles

- **Local and offline first.** No network access is needed for a build.
- **Standard library only.** The optional PDF backend is isolated from the core.
- **Deterministic by construction.** Sorted discovery, canonical JSON, hash-based splits,
  and content-addressed cache entries.
- **Explicit failure.** Strict mode fails on file errors and malformed input. Quality
  thresholds publish to `failed/` instead of replacing `current/`.
- **Verify, do not trust.** The verifier recomputes file hashes and output identity from
  the written bytes.
- **No hidden transformation.** AI processing is not enabled; normalization and schema
  mapping are inspectable configuration.

## Documentation

- [Quickstart and customer workflow](docs/quickstart.md)
- [Architecture](docs/architecture.md)
- [Configuration reference](docs/configuration.md)
- [Schemas and mapping](docs/schemas.md)
- [Quality model](docs/quality.md)
- [Benchmark method and smoke result](docs/benchmark.md)
- [Benchmark methodology](docs/benchmark-methodology.md)
- [Performance analysis](docs/performance-analysis.md)
- [Competitive assessment](docs/competitive-assessment.md)
- [Value validation and audit evidence](docs/value-validation.md)
- [Limitations](docs/limitations.md)
- [Commercial validation plan](docs/commercial-plan.md)
- [Roadmap](docs/roadmap.md)
- [Architecture decision records](docs/adr/)

## Testing

```sh
python -m pytest
```

The suite covers configuration validation, discovery, adapters (text, structured, DOCX,
PDF), chunking, schema validation, end-to-end builds, deduplication, splitting, caching,
strict mode, quality failures, provenance, manifest verification, tamper detection, the
CLI, the synthetic benchmark, and the cross-tool comparison path (the reference baseline runs without pandas or DuckDB).

## License

Proprietary. All rights reserved unless a separate written agreement applies.
