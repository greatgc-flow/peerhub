"""M2.0 Generic Extension Host Manifest Schema and Validation.

Adheres strictly to JSON Schema Draft 2020-12 and M2_0_EXTENSION_HOST_CONTRACT.md.
"""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class SchemaValidationError(ValueError):
    """Extension manifest is structurally malformed or contains unexpected properties."""


class ExtensionManifest(BaseModel):
    """Strictly typed extension manifest definition."""

    model_config = ConfigDict(
        extra="forbid",  # Strictly rejects unknown keys (EXT-006)
        frozen=True,
    )

    id: str = Field(..., pattern=r"^ext_[a-z0-9_]+$")
    version: str
    entrypoint: str
    dependencies: list[str] = Field(default_factory=list)
    description: str = ""


def validate_manifest(data: dict[str, Any] | Any) -> ExtensionManifest:
    """Validate a raw manifest dictionary against the M2.0 manifest contract."""
    if not isinstance(data, dict):
        raise SchemaValidationError(f"Manifest must be a dictionary, got {type(data).__name__}")

    try:
        return ExtensionManifest.model_validate(data)
    except ValidationError as e:
        # Extract the precise error message to aid debugging and assertion matching
        errors = [f"{err['loc']}: {err['msg']}" for err in e.errors()]
        raise SchemaValidationError(f"Manifest schema validation failed: {'; '.join(errors)}") from e
