# Configuration reference

DATUM uses one strict TOML file. Unknown keys, wrong types, and unsupported values are
errors, not warnings. Relative paths are resolved against the directory containing the TOML
file, not against the current working directory.

Use `datum validate --config datum.toml` before a build. `datum validate` checks the
configuration, schema, source/output/cache paths, and tokenizer without reading the corpus.

## Minimal configuration

```toml
[datum]
config_version = 1

[source]
path = "input"

[output]
dir = "output"
```

With only these keys, DATUM uses the `text` schema, sidecar provenance, exact deduplication,
no chunking, no splitting, and an enabled per-file cache.

## Root keys

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `completion_separator` | string | `""` | Separator used to split plain-text input into prompt/completion when the `completion` schema is selected |

`completion_separator` is the only top-level key outside a table. It must appear before the
first table header.

## `[datum]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `config_version` | integer | `1` | Configuration schema version; only `1` is supported |

## `[source]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `path` | string | required | Source directory |
| `recursive` | boolean | `true` | Recurse into subdirectories |
| `include_hidden` | boolean | `false` | Include hidden files and directories |
| `exclude` | array of strings | `[]` | Glob patterns matched against relative paths or basenames |
| `follow_symlinks` | boolean | `false` | Follow file symlinks; targets must stay inside the source root |
| `encoding` | string | `"auto"` | Text encoding or `auto`; BOM detection is automatic |
| `encoding_errors` | string | `"strict"` | `strict` or `replace` |
| `detect_content` | boolean | `true` | Use content signals in addition to extensions |
| `text_fallback` | boolean | `false` | Treat otherwise unsupported non-binary files as text |
| `ordering` | string | `"path"` | Only `path` is supported in `0.1.0` |
| `csv_delimiter` | string | `"auto"` | `auto`, `comma`, `tab`, `semicolon`, `pipe`, or one character |
| `csv_has_header` | boolean | `true` | Interpret the first CSV/TSV row as field names |
| `json_records_path` | string | `""` | Dot path to an array inside a JSON document |
| `jsonl_skip_blank` | boolean | `true` | Skip blank JSONL lines instead of reporting them |

Hidden files are counted in quality reports. Excluded files are counted as
`excluded_by_config`. Unsupported files are skipped by default and counted as `skipped`.

## `[limits]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `max_file_bytes` | integer | `67108864` (64 MiB) | Maximum source file size |
| `max_records_per_file` | integer | `1000000` | Maximum records extracted from one file |
| `max_archive_uncompressed_bytes` | integer | `268435456` (256 MiB) | DOCX/PDF archive expansion limit |
| `min_free_disk_bytes` | integer | `52428800` (50 MiB) | Refuse to build below this free-space guard |

## `[normalize]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `line_endings` | string | `"lf"` | `lf` or `preserve` |
| `unicode_form` | string | `"none"` | `none`, `NFC`, or `NFKC` |
| `strip_outer_whitespace` | boolean | `true` | Trim leading/trailing whitespace of text values |
| `strip_control_chars` | boolean | `true` | Remove disallowed control characters |
| `collapse_whitespace` | boolean | `false` | Collapse runs of whitespace |
| `strip_trailing_whitespace` | boolean | `false` | Remove trailing spaces on each line |

Normalization applies recursively to strings in structured records, except fields mapped
with `preserve_string` behavior (classification labels).

## `[chunking]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `enabled` | boolean | `false` | Enable chunking of text content |
| `unit` | string | `"chars"` | `chars` or `tokens` |
| `size` | integer | `4000` | Chunk size in configured units; must be at least 1 |
| `overlap` | integer | `0` | Overlap; must be smaller than `size` |
| `tokenizer` | string | `""` | `module:attribute` for token chunking |

For `unit = "chars"`, `size` must not exceed `length.max_chars`. For token chunking, the
referenced object must expose `encode(text)` and `decode(tokens)`, or a callable returning
such an object. Tokenizer imports are a dependency boundary; failures are configuration
errors.

## `[length]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `min_chars` | integer | `1` | Minimum content length |
| `max_chars` | integer | `1000000` | Maximum content length |
| `oversize` | string | `"reject"` | `reject` or `truncate` |

`truncate` is only valid with `schema.name = "text"`. Truncated records are retained, counted
as `truncated_records`, and marked in provenance flags. `min_chars` applies after truncation
and chunking.

## `[schema]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | string | `"text"` | Built-in schema name or `custom` |
| `version` | integer | `1` | Schema version; built-ins currently support only `1` |
| `file` | string | absent | Custom schema JSON path; required only for `custom` |

Built-in schemas: `text`, `completion`, `instruction`, `chat`, `qa`, `classification`.
See [schemas](schemas.md).

## `[mapping.fields]`

Maps output field names to dot paths in structured input. Values may use `@text` for plain
text and array indexes such as `choices.0.text`.

```toml
[mapping.fields]
text = "body"
label = "category"
```

Keys must exist in the selected schema. Defaults are the schema field names.

## `[dedup]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `enabled` | boolean | `true` | Drop exact duplicate exported records |
| `index` | string | `"memory"` | `memory` or `sqlite` |

Duplicates are detected on canonical JSON of the mapped output record, after normalization
and length filtering, and before splitting. The first occurrence in deterministic compile
order is retained. The SQLite index is intended for corpora whose digest set does not fit
comfortably in memory.

## `[split]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `enabled` | boolean | `false` | Write train/validation/test files |
| `seed` | integer | `42` | Non-negative split seed |
| `train` | number | `0.9` | Train fraction |
| `validation` | number | `0.05` | Validation fraction |
| `test` | number | `0.05` | Test fraction |

Fractions must be between 0 and 1 and sum to 1.0. Assignment uses a hash of the seed and
the record content digest, so it is stable across files, worker counts, and rebuilds.

## `[provenance]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `mode` | string | `"sidecar"` | `sidecar`, `inline`, `both`, or `off` |
| `inline_field` | string | `"_datum"` | Field name injected into each dataset record for inline/both |
| `include_file_sha256` | boolean | `true` | Include source file SHA-256 in provenance |

Inline provenance is stripped by the verifier before schema validation. It must not collide
with a mapped field.

## `[output]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `dir` | string | `"datum-output"` | Output root containing `current/`, `previous/`, `failed/` |

Staging and spool directories are created inside `output.dir`. Output must not overlap the
source directory or cache directory. `datum clean` refuses to remove paths outside
`output.dir`.

## `[quality]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `include_samples` | boolean | `false` | Attach redacted previews to rejected entries |
| `sample_chars` | integer | `160` | Maximum preview length |
| `max_samples` | integer | `20` | Maximum previews in the rejection log |
| `min_exported_records` | integer | `1` | Required exported record count |
| `max_file_error_rate` | number | `1.0` | Failed files divided by discovered files |
| `max_schema_rejection_rate` | number | `1.0` | Schema-invalid chunks divided by schema-checked chunks |
| `max_duplicate_rate` | number | `1.0` | Duplicates divided by retained pre-dedup candidates |

All rates must be between 0 and 1. Threshold failure does not discard artifacts; it publishes
the build under `failed/` and leaves `current/` unchanged. Critical accounting invariants
fail the build outright. See [quality](quality.md).

## `[errors]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `policy` | string | `"permissive"` | `permissive` or `strict` |
| `unsupported` | string | `"skip"` | `skip` or `fail` |

Strict mode fails a file on extraction errors and malformed input records, and then fails
the build. Mapping and schema rejections remain record-level outcomes; they do not abort the
build unless quality thresholds fail. With `unsupported = "fail"`, an unsupported file is a
file error instead of a skip.

## `[cache]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `enabled` | boolean | `true` | Enable the per-file extraction cache |
| `dir` | string | absent | Cache directory; defaults under the output directory |
| `verify_integrity` | boolean | `true` | Re-hash cached spool files on read |

The cache key includes source digest, adapter name/version, file format, pipeline
fingerprint, schema fingerprint, DATUM version, and cache schema version. Invalid entries
are misses; cache write failures are reported but do not fail the build.

## `[runtime]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `workers` | integer | `0` | Worker threads; `0` chooses automatically, positive values are explicit |
| `progress` | boolean | `true` | Emit progress on a TTY |

Automatic selection is bounded by CPU count, memory, and file count, and uses sequential
processing for very small source trees. Explicit worker counts are capped at 64.

## `[pdf]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `engine` | string | `"builtin"` | `builtin`, `pypdf`, or `auto` |

The built-in engine handles text operators and uncompressed or Flate streams. `pypdf` is
optional and must be installed separately. `auto` prefers `pypdf` when available.

## `[ai]`

AI processing is not implemented in `0.1.0`. The table exists for forward compatibility, but
`ai.enabled = true` is rejected. Leave it unset or use `enabled = false`.

| Key | Type | Default |
| --- | --- | --- |
| `enabled` | boolean | `false` |
| `provider` | string | `""` |
| `model` | string | `""` |
| `max_calls` | integer | `0` |
| `api_key_env` | string | `""` |
| `base_url` | string | `""` |

## `[manifest]`

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `include_input_hashes` | boolean | `true` | Include SHA-256 values for source files |

## Command-line overrides

`datum build` accepts overrides that do not modify the TOML file:

- `--output` replaces `output.dir`.
- `--workers` replaces `runtime.workers`.
- `--cache-dir` replaces `cache.dir`.
- `--strict` selects strict error policy.
- `--no-cache` disables the cache for this build.
- `--no-progress` disables TTY progress.

Overrides that change processing behavior are reflected in the resolved configuration and
fingerprints.
