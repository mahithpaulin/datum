# Benchmark run `tierA-csv-10-opt5`

- Source commit: `431d3ba5aea240295cd6a65b8a365d7d753ec023`
- Profile/tier: `csv-transform` / `A`
- Fixture: 1 files, 10511219 bytes

| Mode | Tool | Runs | Median s | Min s | Max s | IQR s | Peak MiB | Records | Reference match |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| equivalent | python | 5 | 10.471563 | 10.423123 | 10.58011 | 0.005475 | 107.21 | 98625 | True |
| equivalent | datum | 5 | 35.616547 | 35.200601 | 35.665086 | 0.142873 | 40.38 | 98625 | True |
| workflow | datum | 5 | 46.122527 | 45.819904 | 47.580921 | 0.206554 | 41.86 | 98625 | - |

Wall time includes process startup and teardown. DATUM performs a full build with validation, manifest, quality, and verification; competitor rows are the declared transform only unless the row is marked workflow.
