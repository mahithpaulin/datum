"""Exception hierarchy and stable process exit codes."""

from __future__ import annotations


class DatumError(Exception):
    """Base error for all expected DATUM failures."""

    exit_code = 1
    error_type = "datum_error"


class UsageError(DatumError):
    exit_code = 3
    error_type = "usage_error"


class ConfigError(UsageError):
    error_type = "config_error"


class InputError(DatumError):
    error_type = "input_error"


class ExtractionError(DatumError):
    error_type = "extraction_error"


class UnsupportedFormatError(ExtractionError):
    error_type = "unsupported_format"


class SchemaDefinitionError(ConfigError):
    error_type = "schema_definition_error"


class SchemaValidationError(DatumError):
    error_type = "schema_validation_error"


class BuildError(DatumError):
    error_type = "build_error"


class QualityGateError(BuildError):
    exit_code = 2
    error_type = "quality_gate_error"


class VerificationError(DatumError):
    error_type = "verification_error"


class BuildCancelled(BuildError):
    exit_code = 130
    error_type = "build_cancelled"


class DependencyError(ConfigError):
    error_type = "dependency_error"
