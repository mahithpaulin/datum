# Local Tier A before/after evidence

These reports are the compact evidence for the normalization optimization described in
`docs/performance-analysis.md`. Generated outputs and fixture data are intentionally not
committed.

- `before/`: run `tierA-csv-10`, source `52ea9da`, original normalization.
- `after/`: run `tierA-csv-10-opt5`, source `431d3ba`, compiled `Cc` substitution and
  cheaper stat accumulation.

Both runs used the same 10,511,219-byte CSV fixture and the Termux host. The before run was
limited to three measured repetitions with one warm-up; the after run used five measured
repetitions and no warm-up. Compare medians, not the raw repetition counts. The canonical
DATUM output hash is identical. Absolute times are not portable.
