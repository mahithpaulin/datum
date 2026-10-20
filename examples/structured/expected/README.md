# Structured example snapshot

This snapshot was produced from `examples/structured/datum.toml` with DATUM 0.1.0.

| Property | Value |
| --- | --- |
| Build ID | `c384ce7368564f71f7547b7aa3dd405f` |
| Output identity | `86a1fb65f09b78700b0962786a567f395f11bfb3446fbe5649bd911fc2422c6d` |
| Source records | 12 |
| Exported records | 8 |
| Rejected records | 4 (1 duplicate, 2 schema-invalid, 1 too short) |
| Splits | train 6, validation 1, test 1 |

Verify the committed bytes without trusting the original source paths:

```sh
python -m datum.cli verify examples/structured/expected
```

The manifest records absolute paths from the machine that produced the snapshot. Source-path
verification with `--sources` therefore requires the same checkout location, or a rebuild.
