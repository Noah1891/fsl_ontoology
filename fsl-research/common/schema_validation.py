"""Shared JSON Schema validation used across experiment pipelines."""

from jsonschema import Draft202012Validator, FormatChecker


def validate_against_schema(instance: dict, schema: dict) -> list[str]:
    """Return a list of human-readable error messages, empty if valid."""
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.path))
    return [error.message for error in errors]
