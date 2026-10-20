# Quickstart and customer workflow

This page is the shortest path from raw files to a dataset that can be reviewed, verified,
and handed to a downstream job. It assumes DATUM 0.1.0.

## Install

DATUM has no required runtime dependencies. Run it from a checkout:

```sh
git clone https://github.com/mahithpaulin/datum.git
cd datum
python -m datum.cli version
```

To install the `datum` console script in a virtual environment:

```sh
python -m pip install -e .
datum version
```

The editable install needs the `setuptools` build backend. In an offline environment without
it, use `python -m datum.cli` from the repository root.

## Run a known example

Text example:

```sh
python -m datum.cli build --config examples/datum.toml
python -m datum.cli verify examples/output/example/current --sources
```

Structured custom-schema example:

```sh
python -m datum.cli build --config examples/structured/datum.toml
python -m datum.cli verify examples/structured/output/structured/current --sources
```

The committed snapshots under `examples/expected/` and `examples/structured/expected/` can be
verified without the original source paths:

```sh
python -m datum.cli verify examples/expected
python -m datum.cli verify examples/structured/expected
```

## The normal workflow

### 1. Validate the configuration

```sh
python -m datum.cli validate --config path/to/datum.toml
```

This checks configuration keys, schema references, output/source separation, and the
tokenizer if token chunking is configured. It does not build anything.

### 2. Inspect discovery

```sh
python -m datum.cli inspect --config path/to/datum.toml
```

Use this before a large build to confirm file counts, detected formats, unsupported files,
and exclusions. `--limit` controls the number of examples; `--json` is machine-readable.

### 3. Build

```sh
python -m datum.cli build --config path/to/datum.toml
```

A successful build publishes atomically to `<output.dir>/current/`. The previous successful
build moves to `previous/`. A quality failure is published to `failed/<build_id>-<timestamp>/`
instead of replacing `current/`.

Use `--json` for a machine-readable build summary.

### 4. Review the quality gate

Open `quality.txt` or `quality.json` inside the build directory. The quality report reconciles
source records, mapped records, chunks, duplicates, rejects, and exported records.

A quality failure exits nonzero and preserves the existing `current/` dataset. It is not a
partial success. Rejected candidates are still recorded in `rejected.jsonl`.

### 5. Verify independently

```sh
python -m datum.cli verify path/to/output/current --sources
```

Verification recomputes artifact hashes, record counts, schema validity, provenance, split
counts, the rejected log, and the output identity. Use `--sources` when the original source
tree is available. Omit it to verify a copied build directory or committed snapshot.

### 6. Show the manifest

```sh
python -m datum.cli manifest path/to/output/current
```

The manifest records the input file hashes and sizes, resolved configuration, schema, output
hashes, environment, build identity, and output identity. It is the reviewable contract for
the dataset.

### 7. Clean local build state

```sh
python -m datum.cli clean --config path/to/datum.toml --cache --yes
python -m datum.cli clean --config path/to/datum.toml --all --yes
```

`--all` removes current, previous, failed builds, staging work, and the configured cache.

## Point DATUM at your data

A minimal configuration:

```toml
[datum]
config_version = 1

[source]
path = "input"

[schema]
name = "text"

[output]
dir = "output/my-dataset"

[quality]
min_exported_records = 1
```

Replace `[schema]` with one of the built-ins (`text`, `completion`, `instruction`, `chat`,
`qa`, `classification`) or a custom schema. For structured sources, use
`[mapping.fields]` when output field names differ from source paths:

```toml
[schema]
name = "classification"

[mapping.fields]
text = "review.body"
label = "review.sentiment"
```

See [Configuration](configuration.md) and [Schemas and mapping](schemas.md) for every option.

## Use DATUM in CI

A typical CI gate builds, verifies with source hashes, and uploads the evidence:

```yaml
- name: Build dataset
  run: python -m datum.cli build --config path/to/datum.toml --json
- name: Verify dataset
  run: python -m datum.cli verify path/to/output/current --sources --json
- name: Upload build evidence
  uses: actions/upload-artifact@v4
  with:
    name: datum-build
    path: path/to/output/current/
```

Do not treat a zero exit from an unverified build as customer-ready. Treat the manifest,
quality report, and verifier result as the delivery evidence.

## What to hand to a customer

For a completed build, deliver the contents of `current/`:

- `dataset.jsonl` or `train.jsonl`, `validation.jsonl`, `test.jsonl`
- `manifest.json`
- `quality.json` and `quality.txt`
- `provenance.jsonl` when provenance is enabled
- `rejected.jsonl`
- `config.resolved.json`

The recipient can run `python -m datum.cli verify <directory>` without the source tree, or
`--sources` when the original inputs are available.

## Limits to state up front

- DATUM proves mechanical validity and reproducibility, not content truth, licensing, safety,
  or model quality.
- Images, audio, video, HTML, spreadsheets, Parquet, and archives are not parsed in 0.1.0.
- PDF support is text extraction with explicit limits; scanned/image-only PDFs are not OCRed.
- AI-assisted processing is disabled.
- The project is a development preview, not a production support commitment.

See [Limitations](limitations.md) and the [commercial validation plan](commercial-plan.md).
