# Local Tier A before/after evidence

These reports are the compact evidence for the normalization optimization described in
`docs/performance-analysis.md`. Generated outputs and fixture data are intentionally not
committed.

- `before/`: run `tierA-csv-10`, original normalization.
- `after/`: run `tierA-csv-10-opt`, compiled `Cc` substitution and cheaper stat accumulation.

Both runs used the same 10,511,219-byte CSV fixture, three measured repetitions, no warm-up,
and the Termux host. The canonical DATUM output hash is identical. Absolute times are not
portable.
