from benchmarks.compare import run_comparison


def test_cross_tool_comparison_reference_only():
    report = run_comparison(rows=50, text_files=4, text_kib=1, workers=1, repetitions=1, tools=())
    assert report["comparison_schema_version"] == 2
    assert report["fixture"]["csv"]["rows"] == 50
    for workload in report["workloads"].values():
        assert workload["output_content_match"] is True
        assert workload["accuracy"]["all_content_match"] is True
        assert workload["accuracy"]["all_duplicate_free"] is True
        assert all(tool["throughput"]["records_per_second"] > 0 for tool in workload["tools"])
        assert all(tool["peak_rss_mib"] > 0 for tool in workload["tools"])
    assert [tool["tool"] for tool in report["workloads"]["text-ingest"]["tools"]] == [
        "datum",
        "python-reference",
    ]
    assert {item["tool"] for item in report["skipped"]} == {"pandas", "duckdb"}
