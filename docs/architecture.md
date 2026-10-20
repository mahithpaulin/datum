# Architecture

## Scope

DATUM compiles a directory of local files into a versioned dataset build. The architecture
optimizes for four properties, in order:

1. **Correct accounting.** Every input and every candidate record has a visible outcome.
2. **Reproducibility.** Equivalent source bytes plus equivalent configuration produce
   byte-identical dataset files.
3. **Independent verification.** A verifier recomputes hashes and counts from the written
   artifacts instead of trusting build-time counters.
4. **Bounded resource use.** Files are streamed, worker output is spooled to disk, and
   archive expansion is limited.

It does not attempt distributed execution, incremental re-publication, model training, or
semantic data quality judgments.

## Pipeline

```text
source directory
      |
      v
[ discovery ]  sorted paths, exclusions, hidden files, symlinks, hardlink targets
      |
      v
[ detection ]  extension + magic bytes + content classification
      |
      v
[ extraction ] text | delimited | JSON/JSONL | DOCX | PDF  -- malformed records -->
      |                                                        rejected.jsonl
      v
[ mapping ]    built-in/custom schema, normalization, chunking -- mapping errors -->
      |                                                        rejected.jsonl
      v
[ compile ]    length filters, schema validation, exact dedup, hash split
      |
      v
[ export ]     JSONL datasets + provenance sidecar + rejection log
      |
      v
[ quality ]    invariants + thresholds
      |
      v
[ verify ]     independent hash/schema/count/identity check of staging
      |
      v
[ publish ]    current/ or failed/<build_id>-<timestamp>/
```

## Module map

| Module | Responsibility |
| --- | --- |
| `datum/config.py` | Strict TOML model, defaults, path resolution, fingerprints |
| `datum/discovery.py` | Deterministic source walk and duplicate-target suppression |
| `datum/filetypes.py` | Format detection and unsupported-format explanations |
| `datum/ingest/` | Adapter interface and text, delimited, JSON/JSONL, DOCX, PDF extractors |
| `datum/transform/normalize.py` | Line endings, control characters, whitespace, Unicode forms |
| `datum/transform/mapping.py` | Schema mapping, chat/completion handling, chunk emission |
| `datum/transform/chunking.py` | Character or tokenizer-backed chunking |
| `datum/schema.py` | Built-in schemas, custom JSON schema parser, record validation |
| `datum/build.py` | Worker orchestration, spool compilation, quality, verification, publishing |
| `datum/dedup.py` | Exact-content duplicate indexes: memory and SQLite |
| `datum/export.py` | Canonical JSONL writers, hash splits, sidecar and rejection writers |
| `datum/cache.py` | Content-addressed per-file extraction cache |
| `datum/quality.py` | Counters, reconciliation invariants, thresholds, reports |
| `datum/manifest.py` | Build IDs, output identity, environment, manifest construction |
| `datum/verify.py` | Independent manifest and artifact verification |
| `datum/cli.py` | Command-line interface and exit codes |
| `datum/benchmark.py` | Synthetic fixture and three-run mechanics benchmark |

## Determinism model

Deterministic elements:

- Discovery ordering is ascending by relative path.
- Extraction records carry stable source ordinals and locators.
- JSON is canonical: sorted keys, UTF-8, LF endings, compact separators, no NaN/Infinity.
- Record IDs are derived from source path, source digest, record ordinal, and chunk index.
- Splits use `SHA-256(seed + ":" + content_digest)`, not process-random state.
- Cache keys include the source digest, adapter name/version, format, pipeline fingerprint,
  schema fingerprint, DATUM version, and cache schema version.
- Duplicate retention is first-seen in deterministic compilation order.

Nondeterministic elements are deliberately limited to timestamps, durations, host
environment details, and cache hit/miss statistics. These appear in the manifest and
quality report but do not affect dataset bytes or `output_identity`.

The **build ID** is a hash of DATUM version, pipeline version, configuration fingerprint,
schema fingerprint, and the sorted input list with source hashes. The **output identity**
is a hash of the exported files and sidecar, using their paths, hashes, byte sizes, and
record counts. A rebuild can therefore preserve both IDs while manifest timestamps change.

## Build lifecycle

1. Validate configuration, schema, paths, tokenizer, and free disk space.
2. Discover files and compute build identity inputs.
3. Create hidden staging and spool directories under the output directory.
4. Submit files to a bounded thread pool. Workers detect, extract, map, and write draft or
   rejection events to a per-file JSONL spool. They do not assign final record IDs.
5. The main thread reads each spool as a stream, applies truncation and length filters,
   validates against the schema, hashes content, deduplicates, assigns splits, writes
   dataset/sidecar/rejection outputs, and records per-rule counters.
6. Build quality counters and reconciliation invariants.
7. Write preliminary quality files and manifest into staging.
8. Run the independent verifier against staging.
9. Rebuild performance and quality reports with verification time, rewrite final quality
   files and manifest, and publish atomically.
10. Remove spool and staging directories.

Workers are threads because the workload is dominated by file I/O, parsing, and hashing;
Python's shared-memory data model avoids per-record serialization between processes. The
main thread remains the single writer for deterministic ordering, deduplication, and split
assignment.

## Spool format

Each processed file has one spool file under `.datum-spool-*`:

```json
{"event":"draft","draft":{...},"source":{...}}
{"event":"reject","rule":"mapping_error","message":"...","source":{...}}
{"event":"malformed","issue":{...},"source":{...}}
```

Spool files are streamed during compilation, so a large extraction does not need to be held
in memory. The cache stores a copy of the same spool plus metadata and checksums.

## Caching

The cache is opt-in by configuration (enabled by default) and content-addressed:

```text
<cache>/objects/<key[:2]>/<key>/drafts.jsonl
<cache>/objects/<key[:2]>/<key>/meta.json
```

A hit validates metadata, cache schema, adapter, format, pipeline/schema fingerprints,
spool size, and optionally the spool SHA-256. Invalid entries are counted and treated as
misses. Cache writes are atomic via temporary directories. Cache failures are recorded but
do not fail a build unless they prevent reading input or publishing output.

## Publishing and failure semantics

- Successful build: staging is renamed to `current/`; an existing `current/` becomes
  `previous/`.
- Quality failure: staging is renamed to `failed/<build_id>-<timestamp>`; `current/` is not
  replaced.
- Build failure before publish: staging is removed; `current/` and `previous/` are unchanged.
- Strict mode: file errors and malformed input records fail the file/build instead of being
  counted as recoverable issues.
- Critical quality invariant failure raises a build error even if thresholds are loose.

The rename-based publish assumes output and staging are on the same filesystem, which is
enforced by creating staging inside the output directory.

## Trust boundaries and resource controls

- Input is data, never executed. No shell commands, macros, or embedded scripts are run.
- PDF and DOCX parsing have explicit file-size, archive-size, compression-ratio, and
  decompression limits.
- DOCX XML with DTD/entity declarations is rejected.
- Symlinks are skipped by default; when followed, resolved targets must remain inside the
  source root.
- Hardlink/symlink duplicate targets are suppressed during discovery.
- Output paths from manifests are validated against directory traversal before verification.
- Cache and staging paths must not overlap source or output paths in unsafe ways.
- The default file limit is 64 MiB, archive expansion limit is 256 MiB, and minimum free
  disk guard is 50 MiB.

These controls reduce accidental exposure; they are not a sandbox for hostile files. Run
untrusted corpora in an isolated account or container.

## Extension points

- **Adapters:** implement the `Adapter` protocol in `datum/ingest/base.py` and register it
  in `datum/ingest/__init__.py`.
- **Schemas:** add a built-in `SchemaSpec` or supply a custom JSON schema.
- **Chunkers:** character chunking is built in; token chunking requires an object with the
  configured interface.
- **AI providers:** `datum/ai/base.py` defines request/response/provider types, but no
  provider is wired into the build in `0.1.0`. Enabling AI is rejected by configuration.
- **Deduplication backends:** memory and SQLite indexes exist behind the same interface.

Versioning rule: changes to extraction, normalization, mapping, export, or schema behavior
must increment `PIPELINE_VERSION` or the relevant adapter version so stale cache entries
cannot be reused.
