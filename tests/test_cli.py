from __future__ import annotations

import json

from datum import __version__
from datum.cli import main


def test_version(capsys):
    assert main(["version"]) == 0
    output = capsys.readouterr().out
    assert __version__ in output
    assert "Python" in output


def test_cli_end_to_end(capsys, make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "a.txt").write_text("Command line build with a useful record.\n", encoding="utf-8")
    config = make_config()

    assert main(["validate", "--config", str(config), "--json"]) == 0
    validated = json.loads(capsys.readouterr().out)
    assert validated["ok"] is True

    assert main(["build", "--config", str(config), "--dry-run", "--json"]) == 0
    dry_run = json.loads(capsys.readouterr().out)
    assert dry_run["discovered_files"] == 1

    assert main(["build", "--config", str(config), "--json"]) == 0
    built = json.loads(capsys.readouterr().out)
    assert built["ok"] is True
    output = built["output_directory"]

    assert main(["inspect", "--config", str(config), "--json"]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["files"] == 1

    assert main(["verify", output, "--json"]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["ok"] is True

    assert main(["manifest", output, "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["build_id"] == built["build_id"]

    assert main(["clean", "--config", str(config), "--current", "--yes", "--json"]) == 0
    cleaned = json.loads(capsys.readouterr().out)
    assert cleaned["ok"] is True
    assert main(["verify", output]) == 1
    capsys.readouterr()


def test_cli_usage_and_config_errors(capsys, make_config, tmp_path):
    assert main(["inspect", "--limit", "0", str(tmp_path)]) == 3
    capsys.readouterr()
    invalid = make_config()
    invalid.write_text("[datum]\nconfig_version = 999\n", encoding="utf-8")
    assert main(["validate", "--config", str(invalid)]) == 3
    assert "error:" in capsys.readouterr().err


def test_cli_json_error(capsys, tmp_path):
    assert main(["verify", str(tmp_path), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert any("manifest" in error for error in payload["errors"])
