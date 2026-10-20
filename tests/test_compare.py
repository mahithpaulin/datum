from benchmarks.compare import run_comparison


def test_cross_tool_comparison_reference_only():
    report = run_comparison(rows=50, text_files=4, text_kib=1, workers=1, tools=())
    assert report["comparison_schema_version"] == 1
    assert report["fixture"]["csv"]["rows"] == 50
    assert report["workloads"]["text-ingest"]["output_content_match"] is True
    assert report["workloads"]["csv-transform"]["output_content_match"] is True
    assert [tool["tool"] for tool in report["workloads"]["text-ingest"]["tools"]] == [
        "datum",
        "python-reference",
    ]
    assert {item["tool"] for item in report["skipped"]} == {"pandas", "duckdb"}
