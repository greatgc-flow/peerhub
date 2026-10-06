"""Wave 0: EXT-006 and EXT-007 - Extension Manifest Validation & Strict Rejection.

Verifies:
- EXT-006: Manifest config validation strictly rejects unknown keys (Draft 2020-12 / additionalProperties: false).
- EXT-007: Positive control for manifest validation (valid manifest passes and enters VALIDATED).
"""

import pytest

# Target module to be implemented under M2.0 Generic Extension Host
from peerhub.extensions.manifest import (  # type: ignore[import-not-found]
    ExtensionManifest,
    validate_manifest,
    SchemaValidationError,
)


@pytest.mark.schema
def test_ext_006_manifest_validation_rejects_unknown_keys():
    """EXT-006: Crafting an extension manifest with an undefined property raises SchemaValidationError."""
    rogue_manifest_data = {
        "id": "ext_custom_worker",
        "version": "1.0.0",
        "entrypoint": "ext_custom_worker:entrypoint",
        "dependencies": [],
        "description": "A worker extension",
        "rogue_flag": True,  # Undefined key, forbidden by additionalProperties: false
    }

    with pytest.raises(SchemaValidationError, match="rogue_flag"):
        validate_manifest(rogue_manifest_data)


@pytest.mark.schema
def test_ext_007_positive_control_manifest_validation():
    """EXT-007: A well-formed valid extension manifest validates successfully."""
    valid_manifest_data = {
        "id": "ext_artifact_store",
        "version": "1.0.0",
        "entrypoint": "peerhub.extensions.artifact:entrypoint",
        "dependencies": [],
        "description": "Standard M2.1 Artifact Content-Addressable Storage Extension",
    }

    manifest = validate_manifest(valid_manifest_data)
    assert isinstance(manifest, ExtensionManifest)
    assert manifest.id == "ext_artifact_store"
    assert manifest.version == "1.0.0"
