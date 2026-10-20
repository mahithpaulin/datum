"""Build manifest construction and output identity."""

from __future__ import annotations

import hashlib
import os
import platform
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import MANIFEST_SCHEMA_VERSION, PIPELINE_VERSION, __version__
from .config import Config
from .schema import SchemaSpec
from .util import atomic_write_json, canonical_json


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def build_id_for(
    config: Config,
    schema: SchemaSpec,
    inputs: Sequence[Mapping[str, Any]],
) -> str:
    identity_inputs = [
        {
            "path": item.get("path"),
            "sha256": item.get("sha256"),
            "size": item.get("size"),
            "status": item.get("status"),
        }
        for item in sorted(inputs, key=lambda item: str(item.get("path")))
    ]
    return _digest(
        {
            "datum_version": __version__,
            "pipeline_version": PIPELINE_VERSION,
            "config_fingerprint": config.config_fingerprint(),
            "schema_fingerprint": schema.fingerprint(),
            "inputs": identity_inputs,
        }
    )[:32]


def output_identity(outputs: Sequence[Mapping[str, Any]], sidecar: Mapping[str, Any] | None) -> str:
    files = [
        {
            "path": item["path"],
            "sha256": item["sha256"],
            "bytes": item["bytes"],
            "records": item["records"],
        }
        for item in sorted(outputs, key=lambda item: str(item["path"]))
    ]
    if sidecar is not None:
        files.append(
            {
                "path": sidecar["path"],
                "sha256": sidecar["sha256"],
                "bytes": sidecar["bytes"],
                "records": sidecar["records"],
            }
        )
    return _digest(files)


def environment_info() -> dict[str, Any]:
    memory_total = None
    try:
        with open("/proc/meminfo", "r", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    memory_total = int(line.split()[1]) * 1024
                    break
    except OSError:
        memory_total = None
    return {
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "memory_total_bytes": memory_total,
    }


def build_manifest(
    *,
    config: Config,
    schema: SchemaSpec,
    build_id: str,
    status: str,
    started_at: str,
    finished_at: str,
    inputs: Sequence[Mapping[str, Any]],
    outputs: Sequence[Mapping[str, Any]],
    sidecar: Mapping[str, Any] | None,
    rejected: Mapping[str, Any] | None,
    quality_summary: Mapping[str, Any],
    cache_summary: Mapping[str, Any],
    performance: Mapping[str, Any],
    environment: Mapping[str, Any],
    optional_dependencies: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    identity = output_identity(outputs, sidecar)
    transformations = [
        {"name": "discovery", "version": 1, "config": {"ordering": "path", "recursive": config.source.recursive}},
        {"name": "extraction", "version": 1, "adapters": ["text", "delimited", "json", "jsonl", "pdf", "docx"]},
        {"name": "normalize", "version": 1, "config": config.to_dict()["normalize"]},
        {"name": "chunking", "version": 1, "config": config.to_dict()["chunking"]},
        {"name": "mapping", "version": 1, "config": config.to_dict()["mapping"]},
        {"name": "schema_validation", "version": 1, "schema": schema.to_dict()},
        {"name": "exact_deduplication", "version": 1, "config": config.to_dict()["dedup"]},
        {"name": "split", "version": 1, "config": config.to_dict()["split"]},
        {"name": "export", "version": 1, "format": "jsonl", "provenance": config.to_dict()["provenance"]},
    ]
    return {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "datum_version": __version__,
        "pipeline_version": PIPELINE_VERSION,
        "build": {
            "build_id": build_id,
            "output_identity": identity,
            "status": status,
            "started_at": started_at,
            "finished_at": finished_at,
        },
        "config_fingerprint": config.config_fingerprint(),
        "pipeline_fingerprint": config.pipeline_fingerprint(),
        "config": config.to_dict(),
        "schema": schema.to_dict(),
        "environment": dict(environment),
        "dependencies": {
            "required": {},
            "optional": dict(optional_dependencies or {}),
        },
        "inputs": list(inputs),
        "transformations": transformations,
        "ordering": "source path ascending; source record ordinal; chunk index",
        "seed": config.split.seed,
        "outputs": list(outputs),
        "sidecar": dict(sidecar) if sidecar is not None else None,
        "rejected": dict(rejected) if rejected is not None else None,
        "output_identity": identity,
        "quality": dict(quality_summary),
        "cache": dict(cache_summary),
        "performance": dict(performance),
        "notes": [
            "Dataset bytes are deterministic for equivalent inputs and processing configuration.",
            "Timestamps, durations, environment details, and cache statistics make manifest bytes nondeterministic.",
            "Schema validity is a mechanical check, not a guarantee of semantic correctness.",
        ],
    }


def write_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    atomic_write_json(path, manifest)


def summarize_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    build = manifest.get("build", {})
    quality = manifest.get("quality", {})
    outputs = manifest.get("outputs", [])
    return {
        "datum_version": manifest.get("datum_version"),
        "manifest_schema_version": manifest.get("manifest_schema_version"),
        "build_id": build.get("build_id"),
        "status": build.get("status"),
        "output_identity": build.get("output_identity"),
        "started_at": build.get("started_at"),
        "finished_at": build.get("finished_at"),
        "schema": {
            "name": manifest.get("schema", {}).get("name"),
            "version": manifest.get("schema", {}).get("version"),
        },
        "inputs": len(manifest.get("inputs", [])),
        "outputs": [
            {
                "path": item.get("path"),
                "records": item.get("records"),
                "bytes": item.get("bytes"),
                "sha256": item.get("sha256"),
            }
            for item in outputs
        ],
        "quality_status": quality.get("status"),
        "exported_records": quality.get("records", {}).get("exported"),
    }
