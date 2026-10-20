from __future__ import annotations

import json
import zipfile
from pathlib import Path

from datum.build import run_build
from datum.config import load_config
from datum.verify import verify_build


def test_docx_build(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    docx = source / "note.docx"
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/></Types>',
        )
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>DOCX text for DATUM.</w:t></w:r></w:p>"
            "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>cell one</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>cell two</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
            "</w:body></w:document>",
        )
    config = load_config(make_config(), allow_missing_source=False)
    result = run_build(config)
    assert result.status == "passed"
    payload = json.loads((result.output_directory / "dataset.jsonl").read_text(encoding="utf-8"))
    assert payload["text"] == "DOCX text for DATUM.\ncell one\tcell two"
    assert verify_build(result.output_directory).ok


def test_pdf_builtin_build(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    pdf = source / "sample.pdf"
    pdf.write_bytes(
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Length 44 >>\nstream\n"
        b"BT /F1 12 Tf 72 720 Td (Hello PDF world) Tj ET\n"
        b"endstream\nendobj\n"
        b"trailer\n<<>>\n%%EOF\n"
    )
    config = load_config(make_config(), allow_missing_source=False)
    result = run_build(config)
    assert result.status == "passed"
    dataset = (result.output_directory / "dataset.jsonl").read_text(encoding="utf-8")
    assert "Hello PDF world" in dataset
    assert verify_build(result.output_directory).ok


def test_csv_completion_build(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "pairs.csv").write_text(
        'prompt,completion\n"Say, hello","hello there"\n"Name a color","blue"\n',
        encoding="utf-8",
    )
    config = load_config(make_config(schema="completion"), allow_missing_source=False)
    result = run_build(config)
    assert result.status == "passed"
    rows = [
        json.loads(line)
        for line in (result.output_directory / "dataset.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows[0] == {"prompt": "Say, hello", "completion": "hello there"}
    assert verify_build(result.output_directory).ok


def test_chat_json_build(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "chat.json").write_text(
        json.dumps(
            [
                {
                    "messages": [
                        {"role": "user", "content": "What is DATUM?"},
                        {"role": "assistant", "content": "A local data compiler."},
                    ]
                }
            ]
        ),
        encoding="utf-8",
    )
    config = load_config(make_config(schema="chat"), allow_missing_source=False)
    result = run_build(config)
    assert result.status == "passed"
    payload = json.loads((result.output_directory / "dataset.jsonl").read_text(encoding="utf-8"))
    assert payload["messages"][1]["role"] == "assistant"
    assert verify_build(result.output_directory).ok


def test_classification_jsonl_build(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "labels.jsonl").write_text(
        '{"text":"The product is excellent.","label":"positive"}\n'
        '{"text":"The product is terrible.","label":"negative"}\n',
        encoding="utf-8",
    )
    config = load_config(make_config(schema="classification"), allow_missing_source=False)
    result = run_build(config)
    assert result.status == "passed"
    assert result.records["exported"] == 2
    rows = [
        json.loads(line)
        for line in (result.output_directory / "dataset.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows[0]["label"] == "positive"
    assert verify_build(result.output_directory).ok


def test_completion_separator_for_text_input(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "prompt.txt").write_text("Question\n###\nAnswer", encoding="utf-8")
    config = load_config(
        make_config(schema="completion", completion_separator="###"),
        allow_missing_source=False,
    )
    result = run_build(config)
    assert result.status == "passed"
    payload = json.loads((result.output_directory / "dataset.jsonl").read_text(encoding="utf-8"))
    assert payload == {"prompt": "Question", "completion": "Answer"}


def test_structured_text_mapping(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "rows.csv").write_text("body,category\nA long enough body value.,news\n", encoding="utf-8")
    config = load_config(
        make_config(mapping={"text": "body"}),
        allow_missing_source=False,
    )
    result = run_build(config)
    assert result.status == "passed"
    assert json.loads((result.output_directory / "dataset.jsonl").read_text(encoding="utf-8")) == {
        "text": "A long enough body value."
    }


def test_repository_qa_fixture(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    fixture = Path(__file__).parent / "examples" / "ingest" / "faq.csv"
    (source / "faq.csv").write_bytes(fixture.read_bytes())
    config = load_config(make_config(schema="qa"), allow_missing_source=False)
    result = run_build(config)
    assert result.status == "passed"
    assert result.records["exported"] == 3
    rows = [
        json.loads(line)
        for line in (result.output_directory / "dataset.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows[0]["question"] == "What is DATUM?"
    assert verify_build(result.output_directory).ok


def test_repository_completion_fixture_with_duplicate_and_malformed(make_config, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    fixture = Path(__file__).parent / "examples" / "ingest" / "pairs.jsonl"
    (source / "pairs.jsonl").write_bytes(fixture.read_bytes())
    config = load_config(make_config(schema="completion"), allow_missing_source=False)
    result = run_build(config)
    assert result.status == "passed"
    assert result.records["exported"] == 2
    assert result.records["duplicates"] == 1
    assert result.records["malformed_input"] == 1
    assert verify_build(result.output_directory).ok
