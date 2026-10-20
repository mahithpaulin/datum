# Benchmark run `tierA-csv-10`

- Source commit: `52ea9dabbfb8f24af37287fb805b9db6d113fa39`
- Profile/tier: `csv-transform` / `A`
- Fixture: 1 files, 10511219 bytes

| Mode | Tool | Runs | Median s | Min s | Max s | IQR s | Peak MiB | Records | Reference match |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| equivalent | python | 3 | 13.672994 | 13.671413 | 13.679673 | 0.00413 | 106.07 | 98625 | True |
| equivalent | datum | 3 | 53.545397 | 53.541394 | 54.133669 | 0.296138 | 38.94 | 98625 | True |
| workflow | datum | 3 | 66.366671 | 66.292293 | 66.465845 | 0.086776 | 41.91 | 98625 | - |

Wall time includes process startup and teardown. DATUM performs a full build with validation, manifest, quality, and verification; competitor rows are the declared transform only unless the row is marked workflow.
