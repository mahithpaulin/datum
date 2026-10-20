# Structured review example

This example shows a structured CSV/JSON workflow: nested JSONL records are mapped into a
custom schema, validated, deduplicated, split, and exported with provenance.

Run it from the repository root:

```sh
python -m datum.cli validate --config examples/structured/datum.toml
python -m datum.cli inspect --config examples/structured/datum.toml
python -m datum.cli build --config examples/structured/datum.toml
python -m datum.cli quality examples/structured/output/structured/current
python -m datum.cli verify examples/structured/output/structured/current --sources
python -m datum.cli manifest examples/structured/output/structured/current
```

The input contains 12 records:

| Outcome | Count | Reason |
| --- | ---: | --- |
| Exported | 8 | Valid mapped records |
| Duplicate | 1 | Exact duplicate of the first record |
| Schema invalid | 2 | Unknown sentiment label; rating above 5 |
| Too short | 1 | Missing body leaves only 25 mapped characters |

The build splits the 8 exported records into train 6, validation 1, and test 1.

## Files

- `datum.toml` — source, schema, mapping, split, quality, and output settings.
- `review.schema.json` — custom schema with a closed field set and enum/range checks.
- `input/reviews.jsonl` — sample source records, including deliberate rejects.
- `expected/` — committed verified snapshot of the successful build.

Verify the committed snapshot without depending on this checkout's absolute paths:

```sh
python -m datum.cli verify examples/structured/expected
```

The manifest records the absolute paths of the build machine, so `--sources` against the
snapshot only works when the source still exists at those paths.
