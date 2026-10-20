"""Dataset schemas, custom schema loading, and record validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .config import Config
from .errors import SchemaDefinitionError
from .util import canonical_json

SCHEMA_TYPES = ("string", "integer", "number", "boolean", "array", "object", "scalar", "any")
FIELD_KEYS = {
    "type",
    "required",
    "nullable",
    "min_length",
    "max_length",
    "minimum",
    "maximum",
    "enum",
    "min_items",
    "max_items",
    "items",
    "properties",
    "additional_properties",
    "description",
}
SCHEMA_KEYS = {"name", "version", "description", "fields", "additional_properties"}


@dataclass(frozen=True)
class FieldSpec:
    type: str
    required: bool = False
    nullable: bool = False
    min_length: int | None = None
    max_length: int | None = None
    minimum: float | None = None
    maximum: float | None = None
    enum: tuple[Any, ...] | None = None
    min_items: int | None = None
    max_items: int | None = None
    items: "FieldSpec | None" = None
    properties: Mapping[str, "FieldSpec"] | None = None
    additional_properties: bool = False
    description: str = ""


@dataclass(frozen=True)
class SchemaSpec:
    name: str
    version: int
    fields: Mapping[str, FieldSpec]
    additional_properties: bool = False
    description: str = ""
    kind: str = "builtin"

    def to_dict(self) -> dict[str, Any]:
        def field_dict(spec: FieldSpec) -> dict[str, Any]:
            result: dict[str, Any] = {"type": spec.type}
            if spec.required:
                result["required"] = True
            if spec.nullable:
                result["nullable"] = True
            if spec.min_length is not None:
                result["min_length"] = spec.min_length
            if spec.max_length is not None:
                result["max_length"] = spec.max_length
            if spec.minimum is not None:
                result["minimum"] = spec.minimum
            if spec.maximum is not None:
                result["maximum"] = spec.maximum
            if spec.enum is not None:
                result["enum"] = list(spec.enum)
            if spec.min_items is not None:
                result["min_items"] = spec.min_items
            if spec.max_items is not None:
                result["max_items"] = spec.max_items
            if spec.items is not None:
                result["items"] = field_dict(spec.items)
            if spec.properties is not None:
                result["properties"] = {key: field_dict(value) for key, value in spec.properties.items()}
            if spec.additional_properties:
                result["additional_properties"] = True
            if spec.description:
                result["description"] = spec.description
            return result

        return {
            "name": self.name,
            "version": self.version,
            "kind": self.kind,
            "description": self.description,
            "additional_properties": self.additional_properties,
            "fields": {key: field_dict(value) for key, value in self.fields.items()},
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(canonical_json(self.to_dict()).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LoadedSchema:
    spec: SchemaSpec
    source_path: Path | None = None

    @property
    def mapping_kind(self) -> str:
        return self.spec.kind

    def default_mapping(self) -> dict[str, str]:
        if self.spec.kind == "text":
            return {"text": "@text"}
        if self.spec.kind == "chat":
            return {"messages": "messages"}
        if self.spec.kind == "custom":
            return {name: name for name in self.spec.fields}
        return {name: name for name in self.spec.fields}


def _string_field(required: bool = True) -> FieldSpec:
    return FieldSpec(type="string", required=required)


def builtin_schemas() -> dict[str, SchemaSpec]:
    message = FieldSpec(
        type="object",
        required=True,
        additional_properties=False,
        properties={
            "role": FieldSpec(
                type="string",
                required=True,
                enum=("system", "user", "assistant", "tool"),
            ),
            "content": FieldSpec(type="string", required=True),
            "name": FieldSpec(type="string"),
        },
        description="One conversation message",
    )
    chat = SchemaSpec(
        name="chat",
        version=1,
        kind="chat",
        description="Conversational messages with text content",
        fields={
            "messages": FieldSpec(
                type="array",
                required=True,
                min_items=1,
                items=message,
            )
        },
    )
    return {
        "text": SchemaSpec(
            name="text",
            version=1,
            kind="text",
            description="Plain text training example",
            fields={"text": _string_field()},
        ),
        "completion": SchemaSpec(
            name="completion",
            version=1,
            kind="completion",
            description="Prompt and completion pair",
            fields={"prompt": _string_field(), "completion": _string_field()},
        ),
        "instruction": SchemaSpec(
            name="instruction",
            version=1,
            kind="instruction",
            description="Instruction, optional input, and output",
            fields={
                "instruction": _string_field(),
                "input": FieldSpec(type="string", required=False),
                "output": _string_field(),
            },
        ),
        "chat": chat,
        "qa": SchemaSpec(
            name="qa",
            version=1,
            kind="qa",
            description="Question and answer pair",
            fields={"question": _string_field(), "answer": _string_field()},
        ),
        "classification": SchemaSpec(
            name="classification",
            version=1,
            kind="classification",
            description="Text and scalar label",
            fields={"text": _string_field(), "label": FieldSpec(type="scalar", required=True)},
        ),
        "custom": SchemaSpec(name="custom", version=1, kind="custom", fields={}),
    }


def _require_bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise SchemaDefinitionError(f"{where} must be a boolean")
    return value


def _optional_int(value: Any, where: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SchemaDefinitionError(f"{where} must be a non-negative integer")
    return value


def _optional_number(value: Any, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaDefinitionError(f"{where} must be a number")
    return float(value)


def _parse_field(raw: Any, where: str) -> FieldSpec:
    if not isinstance(raw, dict):
        raise SchemaDefinitionError(f"{where} must be an object")
    unknown = sorted(set(raw) - FIELD_KEYS)
    if unknown:
        raise SchemaDefinitionError(f"unknown field key(s) in {where}: {', '.join(unknown)}")
    field_type = raw.get("type")
    if field_type not in SCHEMA_TYPES:
        raise SchemaDefinitionError(f"{where}.type must be one of: {', '.join(SCHEMA_TYPES)}")
    required = _require_bool(raw.get("required", False), f"{where}.required")
    nullable = _require_bool(raw.get("nullable", False), f"{where}.nullable")
    min_length = _optional_int(raw.get("min_length"), f"{where}.min_length")
    max_length = _optional_int(raw.get("max_length"), f"{where}.max_length")
    if min_length is not None and max_length is not None and min_length > max_length:
        raise SchemaDefinitionError(f"{where}.min_length must not exceed {where}.max_length")
    minimum = _optional_number(raw.get("minimum"), f"{where}.minimum")
    maximum = _optional_number(raw.get("maximum"), f"{where}.maximum")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise SchemaDefinitionError(f"{where}.minimum must not exceed {where}.maximum")
    min_items = _optional_int(raw.get("min_items"), f"{where}.min_items")
    max_items = _optional_int(raw.get("max_items"), f"{where}.max_items")
    if min_items is not None and max_items is not None and min_items > max_items:
        raise SchemaDefinitionError(f"{where}.min_items must not exceed {where}.max_items")
    enum = raw.get("enum")
    enum_tuple: tuple[Any, ...] | None = None
    if enum is not None:
        if not isinstance(enum, list) or not enum:
            raise SchemaDefinitionError(f"{where}.enum must be a non-empty array")
        for item in enum:
            if isinstance(item, (dict, list)):
                raise SchemaDefinitionError(f"{where}.enum values must be scalar")
        enum_tuple = tuple(enum)
    items = None
    if "items" in raw:
        items = _parse_field(raw["items"], f"{where}.items")
    properties = None
    if "properties" in raw:
        props_raw = raw["properties"]
        if not isinstance(props_raw, dict):
            raise SchemaDefinitionError(f"{where}.properties must be an object")
        properties = {}
        for name, prop in props_raw.items():
            if not isinstance(name, str) or not name:
                raise SchemaDefinitionError(f"{where}.properties keys must be non-empty strings")
            properties[name] = _parse_field(prop, f"{where}.properties.{name}")
    extra = _require_bool(raw.get("additional_properties", False), f"{where}.additional_properties")
    description = raw.get("description", "")
    if not isinstance(description, str):
        raise SchemaDefinitionError(f"{where}.description must be a string")
    if field_type == "array" and items is None:
        raise SchemaDefinitionError(f"{where}.items is required for array fields")
    if field_type != "array" and (items is not None or min_items is not None or max_items is not None):
        raise SchemaDefinitionError(f"{where}: items/min_items/max_items are only valid for arrays")
    if field_type == "object" and properties is None:
        properties = {}
    if field_type != "object" and (properties is not None or "additional_properties" in raw):
        raise SchemaDefinitionError(f"{where}: properties/additional_properties are only valid for objects")
    if field_type != "string" and (min_length is not None or max_length is not None):
        raise SchemaDefinitionError(f"{where}: min_length/max_length are only valid for strings")
    if field_type not in ("integer", "number") and (minimum is not None or maximum is not None):
        raise SchemaDefinitionError(f"{where}: minimum/maximum are only valid for numeric fields")
    return FieldSpec(
        type=field_type,
        required=required,
        nullable=nullable,
        min_length=min_length,
        max_length=max_length,
        minimum=minimum,
        maximum=maximum,
        enum=enum_tuple,
        min_items=min_items,
        max_items=max_items,
        items=items,
        properties=properties,
        additional_properties=extra,
        description=description,
    )


def load_custom_schema(path: Path) -> SchemaSpec:
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError as error:
        raise SchemaDefinitionError(f"custom schema file not found: {path}") from error
    except OSError as error:
        raise SchemaDefinitionError(f"cannot read custom schema file {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise SchemaDefinitionError(f"custom schema file is not valid JSON ({path}): {error}") from error
    if not isinstance(raw, dict):
        raise SchemaDefinitionError("custom schema must be a JSON object")
    unknown = sorted(set(raw) - SCHEMA_KEYS)
    if unknown:
        raise SchemaDefinitionError(f"unknown custom schema key(s): {', '.join(unknown)}")
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise SchemaDefinitionError("custom schema.name is required")
    version = raw.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise SchemaDefinitionError("custom schema.version must be a positive integer")
    description = raw.get("description", "")
    if not isinstance(description, str):
        raise SchemaDefinitionError("custom schema.description must be a string")
    fields_raw = raw.get("fields")
    if not isinstance(fields_raw, dict) or not fields_raw:
        raise SchemaDefinitionError("custom schema.fields must be a non-empty object")
    fields: dict[str, FieldSpec] = {}
    for field_name, field_raw in fields_raw.items():
        if not isinstance(field_name, str) or not field_name:
            raise SchemaDefinitionError("custom schema field names must be non-empty strings")
        fields[field_name] = _parse_field(field_raw, f"fields.{field_name}")
    additional = _require_bool(raw.get("additional_properties", False), "custom schema.additional_properties")
    return SchemaSpec(
        name=name,
        version=version,
        description=description,
        fields=fields,
        additional_properties=additional,
        kind="custom",
    )


def load_schema(config: Config) -> LoadedSchema:
    builtins = builtin_schemas()
    if config.schema.name != "custom":
        spec = builtins[config.schema.name]
        if config.schema.version != spec.version:
            raise SchemaDefinitionError(
                f"schema '{spec.name}' version {spec.version} is the only supported version; "
                f"configuration requested version {config.schema.version}"
            )
        loaded = LoadedSchema(spec=spec)
        _validate_mapping(loaded, config.mapping.fields)
        return loaded
    if config.schema.file is None:
        raise SchemaDefinitionError("schema.file is required for a custom schema")
    spec = load_custom_schema(config.schema.file)
    loaded = LoadedSchema(spec=spec, source_path=config.schema.file)
    _validate_mapping(loaded, config.mapping.fields)
    return loaded


def _validate_mapping(schema: LoadedSchema, mapping: Mapping[str, str]) -> None:
    unknown = sorted(set(mapping) - set(schema.spec.fields))
    if unknown:
        raise SchemaDefinitionError(
            f"mapping.fields contains output field(s) not present in schema '{schema.spec.name}': "
            f"{', '.join(unknown)}"
        )


def resolve_mapping(schema: LoadedSchema, mapping: Mapping[str, str]) -> dict[str, str]:
    resolved = schema.default_mapping()
    resolved.update(dict(mapping))
    for field_name, path in resolved.items():
        if not path:
            raise SchemaDefinitionError(f"mapping for field '{field_name}' must not be empty")
    return resolved


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) and not isinstance(value, type(None))


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


def validate_record(data: Any, schema: SchemaSpec, path: str = "$") -> list[str]:
    errors: list[str] = []
    if data is None or not isinstance(data, dict):
        return [f"{path}: top-level record must be an object"]
    if not schema.additional_properties:
        extra = sorted(set(data) - set(schema.fields))
        for key in extra:
            errors.append(f"{path}.{key}: additional property is not allowed")
    for name, field_spec in schema.fields.items():
        field_path = f"{path}.{name}"
        if name not in data:
            if field_spec.required:
                errors.append(f"{field_path}: required field is missing")
            continue
        errors.extend(_validate_value(data[name], field_spec, field_path))
    return errors


def _validate_value(value: Any, spec: FieldSpec, path: str) -> list[str]:
    errors: list[str] = []
    if value is None:
        if spec.nullable:
            return []
        return [f"{path}: null is not allowed"]
    if spec.type == "string":
        if not isinstance(value, str):
            return [f"{path}: expected string"]
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return [f"{path}: string is not valid UTF-8"]
        if spec.min_length is not None and len(value) < spec.min_length:
            errors.append(f"{path}: string shorter than min_length {spec.min_length}")
        if spec.max_length is not None and len(value) > spec.max_length:
            errors.append(f"{path}: string longer than max_length {spec.max_length}")
    elif spec.type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return [f"{path}: expected integer"]
        if spec.minimum is not None and value < spec.minimum:
            errors.append(f"{path}: value below minimum {spec.minimum}")
        if spec.maximum is not None and value > spec.maximum:
            errors.append(f"{path}: value above maximum {spec.maximum}")
    elif spec.type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return [f"{path}: expected number"]
        if spec.minimum is not None and value < spec.minimum:
            errors.append(f"{path}: value below minimum {spec.minimum}")
        if spec.maximum is not None and value > spec.maximum:
            errors.append(f"{path}: value above maximum {spec.maximum}")
    elif spec.type == "boolean":
        if not isinstance(value, bool):
            return [f"{path}: expected boolean"]
    elif spec.type == "scalar":
        if not _is_scalar(value):
            return [f"{path}: expected scalar string, number, or boolean"]
    elif spec.type == "array":
        if not isinstance(value, list):
            return [f"{path}: expected array"]
        if spec.min_items is not None and len(value) < spec.min_items:
            errors.append(f"{path}: array shorter than min_items {spec.min_items}")
        if spec.max_items is not None and len(value) > spec.max_items:
            errors.append(f"{path}: array longer than max_items {spec.max_items}")
        assert spec.items is not None
        for index, item in enumerate(value):
            errors.extend(_validate_value(item, spec.items, f"{path}[{index}]"))
    elif spec.type == "object":
        if not isinstance(value, dict):
            return [f"{path}: expected object"]
        properties = spec.properties or {}
        if not spec.additional_properties:
            for key in sorted(set(value) - set(properties)):
                errors.append(f"{path}.{key}: additional property is not allowed")
        for name, child in properties.items():
            child_path = f"{path}.{name}"
            if name not in value:
                if child.required:
                    errors.append(f"{child_path}: required field is missing")
                continue
            errors.extend(_validate_value(value[name], child, child_path))
    elif spec.type == "any":
        pass
    if spec.enum is not None and not any(_json_equal(value, candidate) for candidate in spec.enum):
        errors.append(f"{path}: value is not one of the allowed enum values")
    return errors


def _collect_string_lengths(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(_collect_string_lengths(item) for item in value.values())
    if isinstance(value, list):
        return sum(_collect_string_lengths(item) for item in value)
    return 0


def record_content_length(data: Mapping[str, Any], schema: SchemaSpec) -> int:
    kind = schema.kind
    if kind == "chat":
        messages = data.get("messages")
        if not isinstance(messages, list):
            return 0
        total = 0
        for message in messages:
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                total += len(message["content"])
        return total
    if kind == "classification":
        text = data.get("text")
        return len(text) if isinstance(text, str) else 0
    if kind == "text":
        text = data.get("text")
        return len(text) if isinstance(text, str) else 0
    return _collect_string_lengths(data)


def schema_spec_from_dict(raw: Mapping[str, Any]) -> SchemaSpec:
    if not isinstance(raw, Mapping):
        raise SchemaDefinitionError("manifest schema must be an object")
    name = raw.get("name")
    version = raw.get("version")
    if not isinstance(name, str) or not name:
        raise SchemaDefinitionError("manifest schema.name is missing")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise SchemaDefinitionError("manifest schema.version is invalid")
    fields_raw = raw.get("fields")
    if not isinstance(fields_raw, Mapping) or not fields_raw:
        raise SchemaDefinitionError("manifest schema.fields is invalid")
    fields: dict[str, FieldSpec] = {}
    for field_name, field_raw in fields_raw.items():
        if not isinstance(field_name, str) or not field_name:
            raise SchemaDefinitionError("manifest schema field name is invalid")
        fields[field_name] = _parse_field(dict(field_raw), f"schema.fields.{field_name}")
    kind = raw.get("kind", "builtin")
    if not isinstance(kind, str):
        raise SchemaDefinitionError("manifest schema.kind must be a string")
    description = raw.get("description", "")
    if not isinstance(description, str):
        raise SchemaDefinitionError("manifest schema.description must be a string")
    additional = _require_bool(raw.get("additional_properties", False), "manifest schema.additional_properties")
    return SchemaSpec(
        name=name,
        version=version,
        fields=fields,
        additional_properties=additional,
        description=description,
        kind=kind,
    )
