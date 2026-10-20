# Verified example build

Snapshot of the example build produced from `examples/input/` with `examples/datum.toml`.

- Build ID: `a45d94a913b96579fe9a1e000c531eae`
- Output identity: `a55c1e70f4819a8f21a8a93215504591a6732047c528c406e4c23123f791b9ca`
- Result: 3 discovered files processed, 1 unsupported image skipped, 3 records exported
- Splits: train=2, validation=1, test=0
- Verification: passes without `--sources`; source hashes require this repository's
  `examples/input/` directory.

The manifest contains absolute paths and timestamps from the machine that produced the
snapshot. Dataset bytes and `output_identity` are the reproducibility contract; manifest
bytes are not.

Verify the snapshot from the repository root:

```sh
python -m datum.cli verify examples/expected
```
