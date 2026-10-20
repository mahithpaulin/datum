import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "datum.cli", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_customer_workflow_import_inspect_build_verify_and_tamper(tmp_path: Path):
    source = tmp_path / "input"
    source.mkdir()
    rows = [
        {"faq": {"q": "How do I verify a build?", "a": "Run the verify command with source hashes."}, "tag": "supported"},
        {"faq": {"q": "Can builds run offline?", "a": "Yes, the core pipeline uses no network access."}, "tag": "supported"},
        {"faq": {"q": "How do I verify a build?", "a": "Run the verify command with source hashes."}, "tag": "supported"},
        {
            "faq": {
                "q": "What happens to unknown labels?",
                "a": "They fail schema validation and appear in the rejection log.",
            },
            "tag": "unexpected",
        },
        {"faq": {"q": "What happens to missing fields?"}, "tag": "supported"},
    ]
    (source / "import.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    schema = {
        "name": "faq",
        "version": 1,
        "fields": {
            "question": {"type": "string", "required": True, "min_length": 10},
            "answer": {"type": "string", "required": True, "min_length": 10},
            "label": {"type": "string", "required": True, "enum": ["supported"]},
        },
    }
    (tmp_path / "faq.schema.json").write_text(json.dumps(schema), encoding="utf-8")
    config = tmp_path / "datum.toml"
    config.write_text(
        """[datum]
config_version = 1

[source]
path = "input"

[schema]
name = "custom"
file = "faq.schema.json"

[mapping.fields]
question = "faq.q"
answer = "faq.a"
label = "tag"

[chunking]
enabled = false

[length]
min_chars = 20
max_chars = 2000
oversize = "reject"

[dedup]
enabled = true
index = "memory"

[split]
enabled = false

[provenance]
mode = "sidecar"

[output]
dir = "out"

[quality]
min_exported_records = 2
max_schema_rejection_rate = 0.5
max_duplicate_rate = 0.5

[runtime]
workers = 1
progress = false
""",
        encoding="utf-8",
    )

    validated = run_cli("validate", "--config", str(config), "--json")
    assert validated.returncode == 0, validated.stderr
    assert json.loads(validated.stdout)["ok"] is True

    inspected = run_cli("inspect", "--config", str(config), "--json")
    assert inspected.returncode == 0, inspected.stderr
    inventory = json.loads(inspected.stdout)
    assert inventory["files"] == 1
    assert inventory["formats"] == {"jsonl": 1}
    assert inventory["supported"] == 1
    assert inventory["unsupported"] == []

    built = run_cli("build", "--config", str(config), "--json")
    assert built.returncode == 0, built.stderr
    build = json.loads(built.stdout)
    assert build["status"] == "passed"
    assert build["records"]["exported"] == 2
    assert build["records"]["duplicates"] == 1
    assert build["records"]["schema_rejected"] == 1
    assert build["records"]["mapping_rejected"] == 1

    current = Path(build["output_directory"])
    for name in (
        "dataset.jsonl",
        "manifest.json",
        "quality.json",
        "quality.txt",
        "provenance.jsonl",
        "rejected.jsonl",
        "config.resolved.json",
    ):
        assert (current / name).is_file(), name
    rejected = [
        json.loads(line)
        for line in (current / "rejected.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert {item["rule"] for item in rejected} == {"duplicate", "schema_invalid", "mapping_error"}

    quality = run_cli("quality", str(current), "--json")
    assert quality.returncode == 0, quality.stderr
    quality_report = json.loads(quality.stdout)
    assert quality_report["ok"] is True
    assert quality_report["errors"]["by_code"] == {"mapping_error": 1, "schema_invalid": 1}
    assert quality_report["rejected"]["by_rule"]["mapping_error"] == 1

    verified = run_cli("verify", str(current), "--sources", "--json")
    assert verified.returncode == 0, verified.stdout
    assert json.loads(verified.stdout)["ok"] is True

    dataset = current / "dataset.jsonl"
    dataset.write_text(dataset.read_text(encoding="utf-8") + '{"question":"tampered"}\n', encoding="utf-8")
    tampered = run_cli("verify", str(current), "--json")
    assert tampered.returncode == 1
    report = json.loads(tampered.stdout)
    assert report["ok"] is False
    assert any(check["name"] == "output_sha256" and check["passed"] is False for check in report["checks"])
