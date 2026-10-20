# Benchmark run `tierA-csv-10-opt`

- Source commit: `fafac57ea1b0d1cc1ddf5ed36dded8e3103161d7`
- Profile/tier: `csv-transform` / `A`
- Fixture: 1 files, 10511219 bytes

| Mode | Tool | Runs | Median s | Min s | Max s | IQR s | Peak MiB | Records | Reference match |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| equivalent | python | 3 | 13.06724 | 10.565899 | 15.014967 | 2.224534 | 106.1 | 98625 | True |
| equivalent | datum | 3 | 36.549555 | 36.395978 | 36.597391 | 0.100707 | 38.95 | 98625 | True |
| workflow | datum | 3 | 46.50197 | 45.150431 | 46.600724 | 0.725147 | 42.23 | 98625 | - |

Wall time includes process startup and teardown. DATUM performs a full build with validation, manifest, quality, and verification; competitor rows are the declared transform only unless the row is marked workflow.
