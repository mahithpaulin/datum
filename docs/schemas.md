# Schemas and mapping

A schema defines the exact shape of each exported dataset record. DATUM validates every
candidate chunk against the selected schema before deduplication and export. Schema validity
is mechanical: it says nothing about whether the content is true, useful, licensed, or safe
to train on.

## Built-in schemas

All built-ins use `additional_properties = false`, so unexpected fields are rejected.

### `text`

```json
{"text": "A plain text training example."}
```

| Field | Type | Required |
| --- | --- | --- |
| `text` | string | yes |

Plain text inputs map to this schema automatically. Structured inputs use the `text` field
path, defaulting to a source field named `text`.

### `completion`

```json
{"prompt": "Question", "completion": "Answer"}
```

| Field | Type | Required |
| --- | --- | --- |
| `prompt` | string | yes |
| `completion` | string | yes |

For structured input, both fields are read from source paths named `prompt` and `completion`
unless overridden. For plain text, set a top-level `completion_separator`; the text is split
once on the first occurrence, and both sides are trimmed.

```toml
completion_separator = "###"

[schema]
name = "completion"
```

### `instruction`

```json
{"instruction": "Summarize the text", "input": "Optional source text", "output": "Summary"}
```

| Field | Type | Required |
| --- | --- | --- |
| `instruction` | string | yes |
| `input` | string | no |
| `output` | string | yes |

### `chat`

```json
{
  "messages": [
    {"role": "user", "content": "What is DATUM?"},
    {"role": "assistant", "content": "A local data compiler."}
  ]
}
```

| Field | Type | Required |
| --- | --- | --- |
| `messages` | array of message objects | yes, at least one |

Each message object requires `role` and `content` and permits an optional `name`. Additional
message keys are not allowed. `role` must be one of `system`, `user`, `assistant`, or
`tool`. `content` must be a string.

Chat records must come from structured input. Plain text cannot invent a conversation.
String content is normalized; other values are left for schema validation to accept or
reject.

### `qa`

```json
{"question": "What is DATUM?", "answer": "A local data compiler."}
```

| Field | Type | Required |
| --- | --- | --- |
| `question` | string | yes |
| `answer` | string | yes |

### `classification`

```json
{"text": "The product is excellent.", "label": "positive"}
```

| Field | Type | Required |
| --- | --- | --- |
| `text` | string | yes |
| `label` | scalar string, number, or boolean | yes |

The label is preserved as a string during normalization so values such as `"0"` are not
coerced or rewritten.

## Mapping structured inputs

Structured records are objects produced by the CSV/TSV, JSON, or JSONL adapters. By default
each output field reads a source field with the same name. `[mapping.fields]` overrides that:

```toml
[schema]
name = "classification"

[mapping.fields]
text = "review.body"
label = "review.sentiment"
```

Path rules:

- Dot notation descends through objects: `a.b.c`.
- Array indexes are numeric segments: `choices.0.text`.
- `@text` is available for plain-text records.
- `$` refers to the entire structured value and is mainly useful for text mapping.

Every mapping key must be a field in the selected schema. A missing required field is a
mapping rejection (`mapping_error`), not a schema rejection. A present but wrongly typed
field reaches schema validation and is counted as `schema_invalid`.

Plain-text records cannot satisfy structured schemas. With the text schema, a non-`@text`
mapping path for plain text is a mapping error rather than an empty record.

## Custom schemas

Set `schema.name = "custom"` and provide a JSON file. The file defines one record shape.

```toml
[schema]
name = "custom"
file = "schemas/article.schema.json"
```

```json
{
  "name": "article",
  "version": 1,
  "description": "An article with metadata",
  "additional_properties": false,
  "fields": {
    "title": {"type": "string", "required": true, "min_length": 3},
    "body": {"type": "string", "required": true},
    "published": {"type": "boolean", "required": true},
    "tags": {
      "type": "array",
      "required": false,
      "max_items": 20,
      "items": {"type": "string", "min_length": 1}
    },
    "meta": {
      "type": "object",
      "required": false,
      "properties": {
        "score": {"type": "number", "minimum": 0, "maximum": 1}
      }
    }
  }
}
```

Top-level keys: `name`, `version`, `description`, `fields`, `additional_properties`. All are
optional except `name`, `version`, and `fields`; unknown keys are errors.

Field keys: `type`, `required`, `nullable`, `min_length`, `max_length`, `minimum`,
`maximum`, `enum`, `min_items`, `max_items`, `items`, `properties`,
`additional_properties`, `description`.

Supported types:

| Type | Valid values | Type-specific constraints |
| --- | --- | --- |
| `string` | JSON string, valid UTF-8 | `min_length`, `max_length`, `enum` |
| `integer` | JSON integer, excluding booleans | `minimum`, `maximum`, `enum` |
| `number` | JSON integer or float, excluding booleans | `minimum`, `maximum`, `enum` |
| `boolean` | JSON boolean | `enum` |
| `scalar` | string, number, or boolean | `enum` |
| `array` | JSON array | `items` required, `min_items`, `max_items` |
| `object` | JSON object | `properties`, `additional_properties` |
| `any` | any JSON value | none |

A field is optional by default. `required = true` means the key must exist; `nullable = true`
allows an explicit JSON `null`. Constraints that do not apply to a type are rejected while
loading the schema, which prevents silently ineffective configuration.

Custom schema field names become default mapping paths. Override any of them with
`[mapping.fields]`.

## Chunk metadata

When chunking is enabled, each emitted chunk carries:

- a zero-based `chunk_index` and total `chunk_count`;
- character offsets for character chunking;
- token offsets for token chunking;
- a `chunked` flag;
- a record ID that includes the chunk index.

Chunk boundaries affect record IDs, splits, and deduplication. Changing chunking changes
the output identity even when the source text is unchanged.

## Length interaction

The length filter applies to the mapped output, using the sum of string lengths for
structured records and the text length for text records. It runs before deduplication and
schema validation. With `length.oversize = "truncate"` and the text schema, content is
truncated to `max_chars` and the record is marked as truncated.

Order of record outcomes:

1. Mapping failure
2. Empty content
3. Too short
4. Too long
5. Schema invalid
6. Duplicate
7. Exported

This order is reflected in quality counters and the rejection log.
