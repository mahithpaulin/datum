"""Seeded synthetic dataset generation for DATUM benchmarks."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import random
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from datum.util import utc_now_iso  # noqa: E402

MIB = 1024 * 1024
GIB = 1024 * MIB

TIER_BYTES = {
    "A": 10 * MIB,
    "B": 100 * MIB,
    "C": 1 * GIB,
    "D": 5 * GIB,
    "E": 10 * GIB,
}

PROFILES = (
    "many-small-text",
    "large-text",
    "jsonl-training",
    "csv-transform",
    "mixed-formats",
    "adversarial-quality",
)

PROFILE_DESCRIPTIONS = {
    "many-small-text": "Thousands of small text files with duplicates, empty files, Unicode, and malformed bytes.",
    "large-text": "Fewer large text documents with character chunking and overlap.",
    "jsonl-training": "Prompt/completion/label JSONL with malformed, missing, duplicate, and schema-invalid rows.",
    "csv-transform": "Prompt/completion CSV with duplicates, null-like fields, and whitespace for equivalent transforms.",
    "mixed-formats": "Text, Markdown, CSV, TSV, JSON, JSONL, DOCX, PDF, and unsupported files in one build.",
    "adversarial-quality": "Malformed, corrupt, overlong, duplicate, unsupported, and unusual-path inputs for accounting checks.",
}

WORDS = (
    "dataset record source schema quality manifest provenance chunk token "
    "compile verify deterministic local offline input output evidence contract "
    "alpha beta gamma delta epsilon zeta theta lambda sigma omega "
    "evidence audit reproduce pipeline ingestion normalization validation "
    "serde index partition boundary ledger snapshot".split()
)

UNICODE_SAMPLES = ("café ☕", "naïve résumé", "日本語のテキスト", "Ελληνικά", "данные", "emoji 🚀")


def tier_bytes(tier: str | None) -> int:
    if tier is None:
        return TIER_BYTES["A"]
    key = tier.upper()
    if key not in TIER_BYTES:
        raise ValueError(f"unknown tier {tier!r}; expected one of {', '.join(TIER_BYTES)}")
    return TIER_BYTES[key]


def _rng(seed: int, label: str) -> random.Random:
    return random.Random(f"{seed}:{label}")


def _text_payload(size: int, rng: random.Random) -> str:
    if size <= 0:
        return ""
    pieces: list[str] = []
    length = 0
    paragraph = 0
    while length < size:
        word_count = rng.randint(8, 40)
        words = [rng.choice(WORDS) for _ in range(word_count)]
        if rng.random() < 0.08:
            words.append(rng.choice(UNICODE_SAMPLES))
        sentence = " ".join(words).capitalize() + "."
        if paragraph and paragraph % 4 == 0:
            sentence = f"Section {paragraph // 4}. {sentence}"
        if rng.random() < 0.04 and pieces:
            sentence = pieces[-1]
        pieces.append(sentence)
        length += len(sentence) + 1
        paragraph += 1
    text = "\n".join(pieces) + "\n"
    encoded = text.encode("utf-8")
    if len(encoded) > size:
        text = encoded[:size].decode("utf-8", "ignore")
        encoded = text.encode("utf-8")
    if len(encoded) < size:
        text += " " * (size - len(encoded))
    return text


def _write_text_file(path: Path, size: int, rng: random.Random, malformed: bool = False) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    if size <= 0:
        path.write_bytes(b"")
        return 0
    text = _text_payload(size, rng)
    data = bytearray(text.encode("utf-8"))
    if malformed and data:
        position = max(1, len(data) // 2)
        data[position] = 0xFF
    path.write_bytes(bytes(data))
    return len(data)


def _file_sizes(target_bytes: int, files: int, rng: random.Random, minimum: int, maximum: int) -> list[int]:
    if files < 1:
        raise ValueError("files must be positive")
    average = max(minimum, target_bytes // files)
    sizes = [max(minimum, int(average * rng.uniform(0.5, 1.5))) for _ in range(files)]
    sizes = [min(maximum, value) for value in sizes]
    difference = target_bytes - sum(sizes)
    cursor = 0
    while difference != 0 and cursor < len(sizes) * 4:
        index = cursor % len(sizes)
        if difference > 0:
            room = maximum - sizes[index]
            step = min(difference, max(0, room))
            sizes[index] += step
            difference -= step
        else:
            room = sizes[index] - minimum
            step = min(-difference, max(0, room))
            sizes[index] -= step
            difference += step
        cursor += 1
    return sizes


def _write_many_small_text(root: Path, target_bytes: int, files: int, seed: int) -> dict[str, Any]:
    rng = _rng(seed, "many-small-text")
    root.mkdir(parents=True, exist_ok=True)
    sizes = _file_sizes(target_bytes, files, rng, 1024, 512 * 1024)
    previous: bytes | None = None
    total = 0
    empty = 0
    duplicated = 0
    malformed = 0
    for index, size in enumerate(sizes):
        path = root / f"docs/{index // 100:04d}/note-{index:06d}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        if index % 37 == 0:
            data = b""
            empty += 1
        elif previous is not None and rng.random() < 0.03:
            data = previous
            duplicated += 1
        else:
            is_malformed = size > 0 and rng.random() < 0.002
            _write_text_file(path, size, _rng(seed, f"many-small-text:{index}"), malformed=is_malformed)
            data = path.read_bytes()
            if is_malformed:
                malformed += 1
        path.write_bytes(data)
        if data:
            previous = data
        total += len(data)
    return {
        "files": files,
        "bytes": total,
        "empty_files": empty,
        "duplicate_files": duplicated,
        "malformed_files": malformed,
    }


def _write_large_text(root: Path, target_bytes: int, files: int, seed: int) -> dict[str, Any]:
    rng = _rng(seed, "large-text")
    root.mkdir(parents=True, exist_ok=True)
    sizes = _file_sizes(target_bytes, files, rng, 1024 * 1024, 64 * MIB)
    total = 0
    for index, size in enumerate(sizes):
        path = root / f"documents/part-{index:04d}.txt"
        total += _write_text_file(path, size, _rng(seed, f"large-text:{index}"))
    return {"files": files, "bytes": total}


def _schema_json(fields: dict[str, Any], name: str = "benchmark") -> dict[str, Any]:
    return {
        "name": name,
        "version": 1,
        "description": "Synthetic benchmark schema",
        "additional_properties": False,
        "fields": fields,
    }


def _write_schema(path: Path, fields: dict[str, Any], name: str = "benchmark") -> None:
    path.write_text(
        json.dumps(_schema_json(fields, name), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _jsonl_row(index: int, rng: random.Random) -> dict[str, Any]:
    prompt = (
        f"Prompt {index}: explain how a deterministic dataset build preserves "
        f"{rng.choice(WORDS)} and {rng.choice(WORDS)}."
    )
    if rng.random() < 0.06:
        prompt += f" {rng.choice(UNICODE_SAMPLES)}"
    completion = (
        f"Completion {index}: write validation evidence, count rejections, and "
        f"verify {rng.choice(WORDS)} before publication."
    )
    return {"prompt": prompt, "completion": completion, "label": rng.choice(["supported", "supported", "review"])}


def _write_jsonl_training(
    root: Path,
    target_bytes: int,
    rows: int,
    seed: int,
    *,
    reach_target: bool = False,
) -> dict[str, Any]:
    rng = _rng(seed, "jsonl-training")
    root.mkdir(parents=True, exist_ok=True)
    counts = {
        "rows": 0,
        "blank_rows": 0,
        "malformed_rows": 0,
        "duplicate_rows": 0,
        "missing_rows": 0,
        "schema_invalid_rows": 0,
        "overlong_rows": 0,
        "valid_rows": 0,
    }
    handle = None
    shard_index = -1
    shard_bytes = 0
    total = 0
    last_valid = ""
    try:
        for index in range(rows):
            if handle is None or shard_bytes >= 64 * MIB:
                if handle is not None:
                    handle.close()
                shard_index += 1
                path = root / f"shards/train-{shard_index:05d}.jsonl"
                path.parent.mkdir(parents=True, exist_ok=True)
                handle = path.open("w", encoding="utf-8", newline="\n")
                shard_bytes = 0
            draw = rng.random()
            line = ""
            if draw < 0.005:
                line = "\n"
                counts["blank_rows"] += 1
            elif draw < 0.01:
                line = f'{{"prompt": "malformed {index}", "completion": \n'
                counts["malformed_rows"] += 1
            elif draw < 0.04 and last_valid:
                line = last_valid
                counts["duplicate_rows"] += 1
            elif draw < 0.07:
                row = _jsonl_row(index, rng)
                if rng.random() < 0.5:
                    row.pop("completion", None)
                else:
                    row.pop("label", None)
                line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                counts["missing_rows"] += 1
            elif draw < 0.10:
                row = _jsonl_row(index, rng)
                row["label"] = "unexpected"
                line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                counts["schema_invalid_rows"] += 1
            elif draw < 0.105:
                row = _jsonl_row(index, rng)
                row["prompt"] = "long " * 3000
                line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                counts["overlong_rows"] += 1
            else:
                row = _jsonl_row(index, rng)
                line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                counts["valid_rows"] += 1
                last_valid = line
            handle.write(line)
            encoded = len(line.encode("utf-8"))
            shard_bytes += encoded
            total += encoded
            counts["rows"] += 1
            if reach_target and counts["rows"] % 256 == 0:
                handle.flush()
                if total >= target_bytes:
                    break
    finally:
        if handle is not None:
            handle.close()
    if total < target_bytes * 0.85:
        scale = max(1, int(rows * target_bytes / max(1, total)))
        if scale > rows:
            extra = _write_jsonl_training_extra(root, target_bytes - total, scale - rows, seed)
            for key in counts:
                counts[key] += extra[key]
            total += extra["bytes"]
    return {**counts, "bytes": total, "files": shard_index + 1}


def _write_jsonl_training_extra(root: Path, missing_bytes: int, rows: int, seed: int) -> dict[str, Any]:
    rng = _rng(seed, "jsonl-training-extra")
    counts = {
        "rows": 0,
        "blank_rows": 0,
        "malformed_rows": 0,
        "duplicate_rows": 0,
        "missing_rows": 0,
        "schema_invalid_rows": 0,
        "overlong_rows": 0,
        "valid_rows": 0,
        "bytes": 0,
        "files": 0,
    }
    shard_index = 0
    shard_bytes = 0
    handle = root / "shards" / f"train-extra-{shard_index:05d}.jsonl"
    shard_handle = handle.open("w", encoding="utf-8", newline="\n")
    try:
        for index in range(rows):
            if shard_bytes >= 64 * MIB:
                shard_handle.close()
                shard_index += 1
                shard_handle = (root / "shards" / f"train-extra-{shard_index:05d}.jsonl").open(
                    "w", encoding="utf-8", newline="\n"
                )
                shard_bytes = 0
            line = json.dumps(_jsonl_row(index, rng), ensure_ascii=False, sort_keys=True) + "\n"
            shard_handle.write(line)
            encoded = len(line.encode("utf-8"))
            shard_bytes += encoded
            counts["bytes"] += encoded
            counts["rows"] += 1
            counts["valid_rows"] += 1
    finally:
        shard_handle.close()
    counts["files"] = shard_index + 1
    return counts


def _write_csv_transform(
    root: Path,
    target_bytes: int,
    rows: int,
    seed: int,
    columns: int = 4,
    *,
    reach_target: bool = False,
) -> dict[str, Any]:
    rng = _rng(seed, "csv-transform")
    root.mkdir(parents=True, exist_ok=True)
    path = root / "pairs.csv"
    counts = {"rows": 0, "empty_rows": 0, "duplicate_rows": 0, "whitespace_rows": 0}
    previous: tuple[str, str] | None = None
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        headers = ["id", "prompt", "completion", "category"]
        if columns > len(headers):
            headers.extend(f"meta_{index}" for index in range(columns - len(headers)))
        writer.writerow(headers)
        index = 0
        while index < rows:
            if index % 41 == 40 and previous is not None:
                prompt, completion = previous
                counts["duplicate_rows"] += 1
            elif index % 97 == 96:
                prompt, completion = "", ""
                counts["empty_rows"] += 1
            elif index % 29 == 28:
                prompt = f"  Prompt {index} with outer whitespace  "
                completion = f"  Completion {index} with café ☕  "
                counts["whitespace_rows"] += 1
            elif index % 53 == 52:
                prompt = f"Prompt {index}: evidence"
                completion = ""
            else:
                prompt = f"Prompt {index}: explain {rng.choice(WORDS)} and {rng.choice(WORDS)}"
                completion = f"Completion {index}: preserve {rng.choice(WORDS)} with café ☕."
            row = [index, prompt, completion, rng.choice(["support", "review"])]
            if columns > len(row):
                row.extend("" for _ in range(columns - len(row)))
            writer.writerow(row)
            if prompt.strip() or completion.strip():
                previous = (prompt, completion)
            counts["rows"] += 1
            index += 1
            if reach_target and index % 256 == 0:
                handle.flush()
                if path.stat().st_size >= target_bytes:
                    break
    return {**counts, "bytes": path.stat().st_size, "files": 1}


def _docx_bytes(text: str) -> bytes:
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    paragraphs = "".join(
        f"<w:p><w:r><w:t>{_xml_escape(part)}</w:t></w:r></w:p>"
        for part in text.split("\n")
        if part
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{paragraphs}</w:body></w:document>"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document)
    return output.getvalue()


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _pdf_bytes(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 11 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1", "replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode())
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(output)


def _completion_text(text: str, separator: str = "\n---\n") -> str:
    stripped = text.rstrip()
    if not stripped:
        return separator.strip()
    midpoint = len(stripped) // 2
    cut = stripped.find("\n", midpoint)
    if cut < 0 or cut >= len(stripped) - 1:
        cut = max(1, midpoint)
    return stripped[:cut] + separator + stripped[cut:].lstrip("\n")


def _write_mixed(root: Path, target_bytes: int, seed: int) -> dict[str, Any]:
    rng = _rng(seed, "mixed-formats")
    root.mkdir(parents=True, exist_ok=True)
    shares = {
        "text": 0.30,
        "markdown": 0.15,
        "csv": 0.10,
        "tsv": 0.10,
        "json": 0.05,
        "jsonl": 0.10,
        "docx": 0.10,
        "pdf": 0.05,
    }
    total = 0
    counts: dict[str, int] = {name: 0 for name in shares}
    counts["unsupported"] = 0
    for name, share in shares.items():
        size = max(4096, int(target_bytes * share))
        if name in ("text", "markdown"):
            suffix = ".txt" if name == "text" else ".md"
            path = root / f"{name}/document-000{suffix}"
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = _completion_text(_text_payload(size, _rng(seed, f"mixed:{name}")))
            path.write_text(payload, encoding="utf-8")
            total += path.stat().st_size
            counts[name] += 1
        elif name in ("csv", "tsv"):
            path = root / f"{name}/records.{name}"
            path.parent.mkdir(parents=True, exist_ok=True)
            delimiter = "," if name == "csv" else "\t"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, delimiter=delimiter, lineterminator="\n")
                writer.writerow(["prompt", "completion", "category"])
                written = 0
                while written < size:
                    prompt = _text_payload(80, rng).replace("\n", " ").strip()
                    completion = _text_payload(80, rng).replace("\n", " ").strip()
                    category = "supported" if rng.random() < 0.8 else "review"
                    line = f"{prompt} {completion}\n"
                    writer.writerow([prompt, completion, category])
                    written += len(line.encode("utf-8"))
                total += path.stat().st_size
            counts[name] += 1
        elif name == "json":
            path = root / "json/records.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            records = [
                {
                    "prompt": _text_payload(250, rng).replace("\n", " ").strip(),
                    "completion": _text_payload(250, rng).replace("\n", " ").strip(),
                }
                for _ in range(max(2, size // 700))
            ]
            path.write_text(json.dumps(records, ensure_ascii=False) + "\n", encoding="utf-8")
            total += path.stat().st_size
            counts[name] += 1
        elif name == "jsonl":
            path = root / "jsonl/records.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="\n") as handle:
                written = 0
                while written < size:
                    line = json.dumps(
                        {
                            "prompt": _text_payload(180, rng).replace("\n", " ").strip(),
                            "completion": _text_payload(180, rng).replace("\n", " ").strip(),
                        },
                        ensure_ascii=False,
                    ) + "\n"
                    handle.write(line)
                    written += len(line.encode("utf-8"))
            total += path.stat().st_size
            counts[name] += 1
        elif name == "docx":
            path = root / "docx/document.docx"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_docx_bytes(_completion_text(_text_payload(size, rng))))
            total += path.stat().st_size
            counts[name] += 1
        elif name == "pdf":
            path = root / "pdf/document.pdf"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_pdf_bytes(_completion_text(_text_payload(min(size, 4000), rng))))
            total += path.stat().st_size
            counts[name] += 1
    unsupported = root / "unsupported/image.png"
    unsupported.parent.mkdir(parents=True, exist_ok=True)
    unsupported.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 128)
    total = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
    counts["unsupported"] += 1
    if total < target_bytes:
        fill = root / "jsonl/fill.jsonl"
        fill.parent.mkdir(parents=True, exist_ok=True)
        with fill.open("w", encoding="utf-8", newline="\n") as handle:
            written = 0
            while total + written < target_bytes:
                line = json.dumps(
                    {
                        "prompt": _text_payload(180, rng).replace("\n", " ").strip(),
                        "completion": _text_payload(180, rng).replace("\n", " ").strip(),
                    },
                    ensure_ascii=False,
                ) + "\n"
                handle.write(line)
                written += len(line.encode("utf-8"))
        counts["jsonl"] += 1
    total = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
    return {**counts, "bytes": total}


def _write_adversarial(root: Path, target_bytes: int, seed: int) -> dict[str, Any]:
    rng = _rng(seed, "adversarial-quality")
    root.mkdir(parents=True, exist_ok=True)
    counts = {
        "valid": 0,
        "duplicate": 0,
        "blank": 0,
        "malformed": 0,
        "missing": 0,
        "schema_invalid": 0,
        "overlong": 0,
        "corrupt_files": 0,
        "unsupported": 0,
    }
    shard = root / "nested/quality-review.jsonl"
    shard.parent.mkdir(parents=True, exist_ok=True)
    last_line = ""
    with shard.open("w", encoding="utf-8", newline="\n") as handle:
        for index in range(max(100, target_bytes // 400)):
            draw = rng.random()
            text = _text_payload(120, rng).replace("\n", " ")
            if draw < 0.04:
                line = "\n"
                counts["blank"] += 1
            elif draw < 0.09:
                line = f'{{"text": "bad {index}"\n'
                counts["malformed"] += 1
            elif draw < 0.18 and last_line:
                line = last_line
                counts["duplicate"] += 1
            elif draw < 0.28:
                line = json.dumps({"text": text, "label": "supported"}, ensure_ascii=False) + "\n"
                counts["missing"] += 1
            elif draw < 0.38:
                line = json.dumps(
                    {"text": text, "label": "unexpected", "score": 3},
                    ensure_ascii=False,
                ) + "\n"
                counts["schema_invalid"] += 1
            elif draw < 0.44:
                line = json.dumps(
                    {"text": "long " * 2000, "label": "supported", "score": 3},
                    ensure_ascii=False,
                ) + "\n"
                counts["overlong"] += 1
            else:
                line = json.dumps(
                    {"text": text, "label": "supported", "score": 4},
                    ensure_ascii=False,
                ) + "\n"
                counts["valid"] += 1
                last_line = line
            handle.write(line)
    unusual = {
        "name with spaces.jsonl": {"text": "Unusual path with spaces. " * 4, "label": "supported", "score": 5},
        "café-データ.jsonl": {"text": "Unicode path and control \u0001 character. " * 3, "label": "supported", "score": 2},
        "leading-dash.jsonl": {"text": "A filename beginning with a dash. " * 4, "label": "supported", "score": 1},
    }
    for name, record in unusual.items():
        path = root / "unusual" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
        counts["valid"] += 1
    csv_path = root / "structured/broken.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text(
        "text,label,score\n"
        + "valid csv row, supported, 3\n"
        + "missing column, supported\n"
        + "too,many,columns,here\n"
        + "another valid row, review, 2\n",
        encoding="utf-8",
    )
    counts["valid"] += 2
    corrupt = {
        "structured/broken.json": b"{not json",
        "structured/broken.docx": b"not a zip",
        "structured/broken.pdf": b"%PDF-1.4\nnot really a pdf\n",
        "unsupported/blob.bin": b"\x00\x01\x02\x03",
    }
    for relative, data in corrupt.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if relative.endswith(".bin"):
            counts["unsupported"] += 1
        else:
            counts["corrupt_files"] += 1
    total = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
    return {**counts, "bytes": total, "files": len([path for path in root.rglob("*") if path.is_file()])}


CONFIG_COMMON = """[datum]
config_version = 1

[source]
path = "input"
encoding_errors = "replace"

[normalize]
line_endings = "lf"
strip_outer_whitespace = true
strip_control_chars = true

[errors]
policy = "permissive"
unsupported = "skip"
"""


def _config_text(profile: str, *, workflow: bool) -> str:
    chunking = "enabled = true\nunit = \"chars\"\nsize = 8000\noverlap = 400" if profile in ("large-text", "mixed-formats") else "enabled = false"
    split = "enabled = true\nseed = 42\ntrain = 0.8\nvalidation = 0.1\ntest = 0.1" if workflow else "enabled = false"
    provenance = "sidecar" if workflow else "off"
    if profile == "csv-transform":
        schema = '[schema]\nname = "completion"'
    elif profile in ("jsonl-training", "adversarial-quality"):
        schema = '[schema]\nname = "custom"\nfile = "schema.json"'
    elif profile == "mixed-formats":
        schema = '[schema]\nname = "completion"'
    else:
        schema = '[schema]\nname = "text"'
    minimum = "5" if profile in ("jsonl-training", "adversarial-quality") else "1"
    maximum = "12000" if profile == "jsonl-training" else ("500" if profile == "adversarial-quality" else "1000000")
    cache = "enabled = true" if workflow else "enabled = false"
    prefix = 'completion_separator = "\\n---\\n"\n\n' if profile == "mixed-formats" else ""
    return (
        prefix
        + CONFIG_COMMON
        + f"\n{schema}\n\n[chunking]\n{chunking}\n\n[length]\nmin_chars = {minimum}\nmax_chars = {maximum}\noversize = \"reject\"\n\n"
        + "[dedup]\nenabled = true\nindex = \"memory\"\n\n"
        + f"[split]\n{split}\n\n[provenance]\nmode = \"{provenance}\"\n\n[output]\ndir = \"output\"\n\n"
        + "[quality]\nmin_exported_records = 1\nmax_file_error_rate = 1.0\nmax_schema_rejection_rate = 1.0\nmax_duplicate_rate = 1.0\n\n"
        + f"[cache]\n{cache}\n\n[runtime]\nworkers = 0\nprogress = false\n"
    )


def _build_plan(
    profile: str,
    tier: str,
    *,
    target_bytes: int | None,
    rows: int | None,
    files: int | None,
    seed: int,
    columns: int,
) -> dict[str, Any]:
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}; expected one of {', '.join(PROFILES)}")
    target = target_bytes if target_bytes is not None else tier_bytes(tier)
    if target < 1:
        raise ValueError("target_bytes must be positive")
    plan: dict[str, Any] = {
        "profile": profile,
        "tier": tier.upper(),
        "seed": seed,
        "target_bytes": target,
        "columns": columns,
    }
    if profile == "many-small-text":
        plan["files"] = files or max(1, round(target / (24 * 1024)))
    elif profile == "large-text":
        plan["files"] = files or max(1, round(target / (8 * MIB)))
    elif profile == "jsonl-training":
        plan["rows"] = rows or max(1, round(target / 220))
        plan["rows_explicit"] = rows is not None
        plan["files"] = files
    elif profile == "csv-transform":
        plan["rows"] = rows or 0
        plan["rows_explicit"] = rows is not None
        plan["files"] = 1
    elif profile == "mixed-formats":
        plan["files"] = files
    elif profile == "adversarial-quality":
        plan["rows"] = rows or max(100, target // 400)
        plan["rows_explicit"] = rows is not None
        plan["files"] = files
    return plan


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(str(path.stat().st_size).encode("ascii"))
        digest.update(b"\0")
        file_digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                file_digest.update(chunk)
        digest.update(file_digest.digest())
    return digest.hexdigest()


def generate(
    profile: str,
    root: str | Path,
    *,
    tier: str = "A",
    target_bytes: int | None = None,
    rows: int | None = None,
    files: int | None = None,
    seed: int = 42,
    columns: int = 4,
    hash_fixture: bool = True,
    reuse: bool = True,
) -> dict[str, Any]:
    base = Path(root).expanduser().resolve()
    plan = _build_plan(
        profile,
        tier,
        target_bytes=target_bytes,
        rows=rows,
        files=files,
        seed=seed,
        columns=columns,
    )
    manifest_path = base / "fixture.json"
    if reuse and manifest_path.is_file() and (base / "input").is_dir():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("plan") == plan and existing.get("status") in ("complete", "generated"):
            return existing
    if base.exists() and any(base.iterdir()):
        raise FileExistsError(f"fixture directory is not empty: {base}")
    base.mkdir(parents=True)
    input_dir = base / "input"
    started = time.perf_counter()
    if profile == "many-small-text":
        counts = _write_many_small_text(input_dir, plan["target_bytes"], plan["files"], seed)
    elif profile == "large-text":
        counts = _write_large_text(input_dir, plan["target_bytes"], plan["files"], seed)
    elif profile == "jsonl-training":
        _write_schema(
            base / "schema.json",
            {
                "prompt": {"type": "string", "required": True, "min_length": 5},
                "completion": {"type": "string", "required": True, "min_length": 5},
                "label": {"type": "string", "required": True, "enum": ["supported", "review"]},
            },
        )
        counts = _write_jsonl_training(
            input_dir,
            plan["target_bytes"],
            plan["rows"],
            seed,
            reach_target=not plan.get("rows_explicit", False),
        )
    elif profile == "csv-transform":
        counts = _write_csv_transform(
            input_dir,
            plan["target_bytes"],
            plan["rows"] or 10**12,
            seed,
            columns,
            reach_target=not plan.get("rows_explicit", False),
        )
    elif profile == "mixed-formats":
        counts = _write_mixed(input_dir, plan["target_bytes"], seed)
    else:
        _write_schema(
            base / "schema.json",
            {
                "text": {"type": "string", "required": True, "min_length": 10},
                "label": {"type": "string", "required": True, "enum": ["supported", "review"]},
                "score": {"type": "integer", "required": True, "minimum": 1, "maximum": 5},
            },
        )
        counts = _write_adversarial(input_dir, plan["target_bytes"], seed)
    (base / "datum.toml").write_text(_config_text(profile, workflow=False), encoding="utf-8")
    (base / "datum-workflow.toml").write_text(_config_text(profile, workflow=True), encoding="utf-8")
    elapsed = time.perf_counter() - started
    manifest = {
        "fixture_schema_version": 1,
        "status": "generated",
        "created_at": utc_now_iso(),
        "plan": plan,
        "profile": profile,
        "description": PROFILE_DESCRIPTIONS[profile],
        "counts": counts,
        "input_bytes": sum(
            path.stat().st_size for path in input_dir.rglob("*") if path.is_file()
        ),
        "input_files": len([path for path in input_dir.rglob("*") if path.is_file()]),
        "generation_seconds": round(elapsed, 6),
        "fixture_sha256": _tree_hash(input_dir) if hash_fixture else None,
        "configs": {
            "equivalent": str(base / "datum.toml"),
            "workflow": str(base / "datum-workflow.toml"),
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a seeded DATUM benchmark fixture.")
    parser.add_argument("--profile", choices=PROFILES, required=True)
    parser.add_argument("--tier", default="A", choices=sorted(TIER_BYTES))
    parser.add_argument("--root", required=True)
    parser.add_argument("--target-bytes", type=int)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--rows", type=int)
    parser.add_argument("--files", type=int)
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument("--hash-fixture", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--reuse", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = generate(
            args.profile,
            args.root,
            tier=args.tier,
            target_bytes=args.target_bytes,
            rows=args.rows,
            files=args.files,
            seed=args.seed,
            columns=args.columns,
            hash_fixture=args.hash_fixture,
            reuse=args.reuse,
        )
    except (OSError, ValueError, FileExistsError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(manifest, indent=2, sort_keys=True))
    else:
        print(f"Generated {manifest['profile']} tier {manifest['plan']['tier']}")
        print(f"Input: {manifest['input_files']} files, {manifest['input_bytes']} bytes")
        print(f"Fixture: {args.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
