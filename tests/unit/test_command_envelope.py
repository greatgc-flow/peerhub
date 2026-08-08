import pytest
from peerhub.core.protocol import (
    CommandEnvelope,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
)
import json

def test_command_envelope_json_roundtrip():
    """A CommandEnvelope encoded to JSON and decoded back should be identical."""
    original = CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id="req-123",
        correlation_id="corr-123",
        client_id="client-abc",
        actor_id="actor-xyz",
        scope={"workspace_id": "ws-1", "home_id": "home-1"},
        method="dispatch.admit",
        params={"prompt": "test prompt", "requested_capabilities": ["cap1"]},
        idempotency_key="idem-key-1",
        expected_policy_revision=42,
        expected_configuration_revision="rev-conf",
        client_timestamp=1690000000000,
    )
    
    import dataclasses
    
    # Encode using manual dict to avoid deepcopy mappingproxy issues
    envelope_dict = {f.name: getattr(original, f.name) for f in dataclasses.fields(original)}
    # Convert mappingproxy to dict so json.dumps can serialize it
    envelope_dict["scope"] = dict(envelope_dict["scope"])
    envelope_dict["params"] = dict(envelope_dict["params"])
    encoded_str = json.dumps(envelope_dict)
    
    # Decode
    decoded_dict = json.loads(encoded_str)
    reconstructed = CommandEnvelope(**decoded_dict)
    
    # Assert
    assert original == reconstructed
    assert original.client_timestamp == reconstructed.client_timestamp
    assert original.scope == reconstructed.scope
    assert original.params == reconstructed.params
