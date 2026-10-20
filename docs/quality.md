# Quality model

DATUM treats quality as accounting first and policy second. The quality report proves that
outcomes reconcile; threshold checks then decide whether a build is acceptable under the
configured policy.

A build can be mechanically consistent and still be a poor dataset. Quality checks do not
measure semantic truth, duplication beyond exact matches, licensing, toxicity, bias, or
downstream model performance.

## File outcomes

Every discovered file has exactly one outcome:

```text
discovered = processed + failed + skipped
```

| Counter | Meaning |
| --- | --- |
| `discovered` | Regular files returned by discovery |
| `processed` | Extracted and compiled, even if some records were rejected |
| `processed_with_errors` | Processed files that produced malformed input or mapping issues |
| `failed` | Detection, extraction, size, or strict-mode failures |
| `skipped` | Unsupported format skipped under `errors.unsupported = "skip"` |
| `empty_files` | Processed files with no records |
| `input_bytes_discovered` | Total bytes discovered |
| `input_bytes_processed` | Bytes from processed files |
| `excluded_by_config` | Files matched by `source.exclude` |
| `hidden_skipped` | Hidden files/directories ignored |
| `symlinks_skipped` | Symlinks ignored because following is disabled |
| `non_regular_skipped` | Sockets, devices, and other non-regular entries |
| `duplicate_targets_skipped` | Multiple paths resolving to the same file identity |
| `walk_issues` | Directory-walk errors |

Hidden, excluded, symlink, and non-regular entries are outside `discovered`; they are
visible in the report but not part of the file accounting equation.

## Record outcomes

Counters are arranged as a reconciliation chain:

```text
source_records_extracted = source_records_mapped + mapping_rejected

chunks_created = empty + too_short + too_long + schema_rejected
                 + duplicates + exported

retained_pre_dedup = chunks_created - empty - too_short - too_long - schema_rejected

exported = retained_pre_dedup - duplicates

rejected_total = mapping_rejected + empty + too_short + too_long
                 + schema_rejected + duplicates

excluded_total = rejected_total + malformed_input
```

| Counter | Meaning |
| --- | --- |
| `source_records_extracted` | Records emitted by adapters |
| `source_records_mapped` | Records successfully mapped to the schema |
| `mapping_rejected` | Records that could not be mapped |
| `chunks_created` | Candidate chunks produced from mapped records |
| `source_records_split` | Mapped source records producing more than one chunk |
| `empty` | Zero-length candidates |
| `too_short` | Candidates below `length.min_chars` |
| `too_long` | Candidates above `length.max_chars` |
| `schema_rejected` | Candidates failing schema validation |
| `duplicates` | Exact duplicates removed |
| `exported` | Records written to dataset files |
| `truncated` | Records retained after truncation |
| `malformed_input` | Source records that could not be parsed as records |

`rejected.jsonl` contains one entry for each `rejected_total` plus each `malformed_input`.
It is not a sample: the verifier checks its line count against the quality report.

Use `datum quality BUILD` to print the status, rejection counts by rule, error examples,
failed thresholds, and failed invariants. Add `--json` for the summary or `--json --full` for
the complete report.

## Rejection and error codes

| Rule | Where it comes from | Meaning |
| --- | --- | --- |
| `malformed_input` | Adapter | Unparseable structured record, invalid line, or similar issue |
| `mapping_error` | Mapping | Missing required source field, wrong shape, or unmappable input |
| `empty` | Compile | Zero content length |
| `too_short` | Compile | Content below the configured minimum |
| `too_long` | Compile | Content above the configured maximum with `oversize = "reject"` |
| `schema_invalid` | Compile | Record fails the selected schema |
| `duplicate` | Dedup | Exact duplicate of an earlier retained record |
| `serialization_error` | Compile | Record cannot be serialized as strict JSON |

Strict mode converts extraction errors and malformed input records into file failures. It
does not convert mapping or schema rejections into build failures; those remain visible
record outcomes. This prevents one bad row from stopping a large corpus while still making
strict handling of unreadable input available.

## Rates

| Rate | Definition |
| --- | --- |
| `file_error_rate` | `failed / discovered` |
| `file_skip_rate` | `skipped / discovered` |
| `schema_rejection_rate` | `schema_rejected / schema_checked` |
| `schema_compliance` | `(schema_checked - schema_rejected) / schema_checked` |
| `mapping_rejection_rate` | `mapping_rejected / source_records_extracted` |
| `record_duplicate_rate` | `duplicates / retained_pre_dedup` |

`schema_checked = chunks_created - empty - too_short - too_long`. A rate with a zero
denominator is reported as `0.0` and `schema_compliance` is `null`.

## Critical invariants

These must hold for any published build:

| Invariant | Equation |
| --- | --- |
| `file_accounting` | discovered = processed + failed + skipped |
| `processed_with_errors_subset` | processed_with_errors <= processed |
| `source_record_mapping` | extracted = mapped + mapping_rejected |
| `candidate_outcomes` | chunks = empty + too_short + too_long + schema_rejected + duplicates + exported |
| `retention_equation` | retained_pre_dedup = chunks - empty - too_short - too_long - schema_rejected |
| `export_equation` | exported = retained_pre_dedup - duplicates |
| `split_accounting` | exported = sum(split counts) |
| `rejected_log_accounting` | rejection log lines = rejected_total + malformed_input |

An invariant failure raises a build error before publishing. It indicates a bug or
corruption, not a data-quality policy decision.

## Thresholds

Thresholds are configurable policy:

- `quality.min_exported_records`
- `quality.max_file_error_rate`
- `quality.max_schema_rejection_rate`
- `quality.max_duplicate_rate`

A threshold failure sets `quality.status = "quality_failed"` and
`manifest.build.status = "quality_failed"`. The artifacts are retained under
`failed/<build_id>-<timestamp>/`; `current/` is not replaced. Exit code is `2`.

Threshold defaults are intentionally permissive (`1.0` rates, one exported record). Production
configurations should set limits appropriate to the corpus and review the report before
publishing.

## Reading `quality.txt`

```text
DATUM quality report (passed)
Build ID: ...
Files: 4 discovered, 3 processed (0 with record errors), 0 failed, 1 skipped
Records: 3 source records, 3 candidate chunks, 3 exported
Exclusions: 0 mapping, 0 empty, 0 too short, 0 too long, 0 schema-invalid, 0 exact duplicates
Malformed input records: 0
Schema: text v1; compliance: 100.00%
Splits: test=0, train=2, validation=1
Lengths: min=220 max=310 p50=272 p99=310
Time: 105 ms; input 805 B; output 853 B
Quality thresholds: passed
```

The machine-readable `quality.json` contains the full counters, rates, invariant rows,
threshold rows, transformations, split counts, length distribution, errors, cache summary,
and performance timings.

## Verification versus quality

Quality explains what the build decided. Verification proves that the published bytes match
the manifest and that the counts can be reconstructed from the files. `datum verify` checks:

- manifest schema version and build status;
- schema definition readability;
- output existence, byte size, SHA-256, line count, and schema validity;
- inline provenance presence when required;
- sidecar checksum, uniqueness of record IDs, and output line references;
- rejected log checksum and parseability;
- quality file version, counts, invariants, and status consistency;
- recomputed output identity;
- optional source hashes with `--sources`.

A successful verify does not imply the dataset passes a quality policy; it means the
artifacts are internally consistent and intact.

## Known blind spots

- Exact deduplication does not detect near-duplicates or paraphrase.
- Schema validation cannot detect wrong labels, hallucinations, or poisoned content.
- Character chunking can split mid-sentence and mid-token.
- Counts describe volume, not information value.
- Quality rates are corpus-wide; a high aggregate can hide a failed subset.
- No PII detection, licensing check, toxicity filter, or language identification exists in
  `0.1.0`.
