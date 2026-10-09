"""Keep the shipped manifest schema aligned with the host's Pydantic model."""

import json
from importlib.resources import files
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from peerhub.extensions.manifest import ExtensionManifest, SchemaValidationError, validate_manifest


SPEC = Path(__file__).resolve().parents[2] / "docs/m1_spec/04_SCHEMAS"
SCHEMA_BYTES = files("peerhub.extensions").joinpath("schemas/extension-manifest.schema.json").read_bytes()
SCHEMA = json.loads(SCHEMA_BYTES)
VALIDATOR = Draft202012Validator(SCHEMA)
EXAMPLE = json.loads((SPEC / "examples/extension-manifest.example.json").read_text(encoding="utf-8"))


def test_manifest_schema_and_model_accept_documented_example():
    assert SCHEMA_BYTES == (SPEC / "extension-manifest.schema.json").read_bytes()
    Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert SCHEMA["additionalProperties"] is False
    model_schema = ExtensionManifest.model_json_schema()
    assert SCHEMA["required"] == model_schema["required"]
    assert SCHEMA["properties"].keys() == model_schema["properties"].keys()
    VALIDATOR.validate(EXAMPLE)
    assert validate_manifest(EXAMPLE).model_dump() == EXAMPLE
    minimal = {key: EXAMPLE[key] for key in SCHEMA["required"]}
    VALIDATOR.validate(minimal)
    manifest = validate_manifest(minimal)
    assert manifest.dependencies == []
    assert manifest.description == ""


@pytest.mark.parametrize("changes", [
    {"rogue_flag": True},
    {"id": "worker"},
    {"id": "ext_Upper"},
    {"dependencies": ["ext_storage>=1.0.0"]},
    {"dependencies": ["ext_storage=="]},
    {"dependencies": ["ext_storage==1.0.*"]},
    {"description": None},
])
def test_manifest_schema_and_model_reject_invalid_fields(changes):
    data = {**EXAMPLE, **changes}
    with pytest.raises(ValidationError):
        VALIDATOR.validate(data)
    with pytest.raises(SchemaValidationError):
        validate_manifest(data)


@pytest.mark.parametrize("missing", ["id", "version", "entrypoint"])
def test_manifest_schema_and_model_reject_missing_required_fields(missing):
    data = {key: value for key, value in EXAMPLE.items() if key != missing}
    with pytest.raises(ValidationError):
        VALIDATOR.validate(data)
    with pytest.raises(SchemaValidationError):
        validate_manifest(data)
