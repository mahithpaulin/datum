# Benchmark evidence

This directory contains compact, reviewable evidence. Generated fixtures, raw dataset
outputs, caches, and multi-gigabyte run directories are intentionally excluded.

- `baseline-52ea9da/`: Phase 0 environment, test log, smoke report, and the checked-in
  cross-tool comparison that preceded the configurable harness.
- `local-tier-a/`: same-host before/after reports for the normalization optimization.
- `comparison-large.json`: aggregated Kaggle Tier A/B/C reports, once downloaded and
  validated.

Raw trial files use JSONL so every measured repetition can be inspected. Do not replace
outliers or failed trials; explain them in the analysis.
