# Limitations

DATUM `0.1.0` is a working vertical slice. This page states what it does not do, so the
scope is not mistaken for a production data platform.

## Product status

- No production SLA, long-term support, migration policy, or security audit.
- No published real-world corpus benchmark or cross-tool comparison.
- No downstream model-quality evaluation. Schema-valid does not mean useful.
- The package is marked proprietary and does not grant an open-source license.
- AI assistance is disabled. The provider interface is a placeholder, not a feature.

## Input coverage

Supported in this release:

- UTF-8-compatible text, Markdown, source code, and common configuration files.
- CSV and TSV with header and delimiter detection.
- JSON arrays/objects and JSONL/NDJSON.
- DOCX paragraphs and tables.
- Text-based PDFs through a limited built-in extractor or optional `pypdf`.

Not supported:

- OCR, images, audio, video, scanned PDFs, or handwritten content.
- Legacy `.doc`, `.xls`, XLSX, ODS, Parquet, Avro, ORC, SQLite databases, or XML/HTML.
- Archive extraction (ZIP, TAR, gzip, 7z). Select an extracted directory instead.
- Email, chat exports, Slack/Teams archives, or database connectors.
- Web crawling, network sources, or authenticated cloud storage.

Unsupported files are reported, not silently dropped. A ZIP that is not a DOCX is rejected
as unsupported; a ZIP with a DOCX shape is parsed as DOCX.

### Text and encoding

- Encoding is auto-detected from BOM, configuration, or UTF-8-compatible decoding. Arbitrary
  legacy encodings may need `source.encoding` and `encoding_errors = "replace"`.
- Replacement characters are counted as encoding replacements, but content loss is not
  otherwise repaired.
- Lone surrogates and invalid Unicode are rejected by strict JSON serialization.
- No language identification, transliteration, or script normalization beyond optional
  NFC/NFKC.

### DOCX

- Only `word/document.xml` body paragraphs and tables are extracted.
- Headers, footers, footnotes, comments, tracked changes, text boxes, drawings, embedded
  objects, and images are not extracted in full.
- Table cells are flattened into tab-separated lines; merged cells and complex layouts may
  lose structure.
- DOCX files with DTD/entity declarations or suspicious compression ratios are rejected.
- The size of the uncompressed container, not just the file, is limited.

### PDF

- The built-in extractor handles basic text operators and Flate-compressed streams. It is not
  a complete PDF implementation.
- Scanned/image-only PDFs produce no text and are reported as empty or extraction failures.
- Fonts, ligatures, right-to-left layout, multi-column order, tables, annotations, and forms
  are not reconstructed reliably.
- Encrypted PDFs and unsupported filters are rejected.
- `pypdf` is optional. With `pdf.engine = "auto"`, behavior can differ between environments
  depending on whether `pypdf` is installed. For reproducibility, pin the engine and record
  the dependency version in the manifest.

## Transformation coverage

- Deduplication is exact only. Near-duplicates, boilerplate, paraphrases, and template
  variants survive.
- The default duplicate index is in memory and proportional to the number of retained
  records. The SQLite option stores hashes on disk but is not benchmarked for very large
  corpora.
- Splitting is hash-based and does not stratify by label, source, language, or time. It is
  not a substitute for leakage-aware dataset design.
- Character chunking can split words, sentences, code blocks, and table rows. Token chunking
  requires an external tokenizer object; none is bundled.
- Normalization is deliberately conservative. It does not fix PDF hyphenation, OCR errors,
  boilerplate, markup remnants, or inconsistent labels.
- Structured adapters coerce only through JSON/CSV parsing; they do not infer dates,
  categories, numeric units, or entity relationships.
- Custom schemas validate shape and simple constraints, not semantics, uniqueness across
  records, referential integrity, or label taxonomies.
- No PII detection, redaction, secret scanning, toxicity filtering, copyright detection, or
  license enforcement exists in the pipeline.

## Execution model

- Worker threads improve I/O overlap but do not bypass the Python GIL for CPU-heavy parsing.
- Process-based parallelism is not supported. On Android/Termux, `ProcessPoolExecutor` is not
  viable in the target environment.
- A file is read into memory for extraction up to `limits.max_file_bytes` (64 MiB default).
  Lower this limit on memory-constrained machines.
- Compilation is single-threaded by design for deterministic output. Large corpora spend
  their time in the compile stage.
- Spool files and staging output require free disk roughly proportional to extracted data.
  A minimum free-space guard refuses builds below `limits.min_free_disk_bytes`.
- The cache caches extraction and mapping, not final compilation. Changing dedup, splits,
  length filters, provenance, or compilation still recompiles every record.
- There is no resume-after-crash, incremental publish, or partial build reuse.
- Discovery is a full walk. There is no file-watching mode or database-backed catalog.

## Reliability and operations

- Publishing uses directory renames and assumes a local filesystem. Cross-filesystem output
  is rejected or fails during publish.
- `current/`, `previous/`, and `failed/` are managed as whole directories. There is no
  retention policy or automatic garbage collection beyond `datum clean`.
- A quality-failed build is retained, but the tool does not notify anyone.
- No metrics endpoint, structured logging backend, scheduler, API server, or multi-user
  service exists.
- No authentication, authorization, encryption-at-rest, or audit log is provided.
- Directory traversal and unsafe manifest paths are checked, but DATUM is not a sandbox for
  hostile input. Use an isolated account or container.
- Termux/Android may suspend or kill long builds when the device sleeps or memory pressure
  rises. Keep corpora small, lower worker counts, and checkpoint work.

## Reproducibility caveats

Dataset bytes are deterministic for the same inputs and processing configuration. The
following can still change results:

- A different DATUM, adapter, schema, or pipeline version.
- A different optional PDF engine or dependency version.
- Files that change between discovery, hashing, and extraction. DATUM detects many such
  races but cannot freeze the filesystem.
- Platform-specific text decoding differences for ambiguous encodings.
- Filesystem behavior for symlinks, hardlinks, Unicode paths, and case folding.
- Environment variables that affect Python, locale, or third-party tokenizers.

Manifest bytes include timestamps, durations, host details, and cache statistics, so they are
not byte-identical across runs. Use `output_identity` and dataset hashes for equality, not
the manifest hash.

## Commercial readiness gaps

Before selling or deploying this as a data platform, the project needs at least:

- Real customer discovery and paid-pilot evidence.
- A measured benchmark on representative corpora and hardware.
- A security review, dependency policy, and incident process.
- Data-processing terms, privacy review, and license/compliance guidance.
- Migration and deprecation policy for schemas, manifests, and cache entries.
- Support boundaries and an escalation path.
- Tests against adversarial and malformed files at larger scale.
