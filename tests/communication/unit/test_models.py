from pathlib import Path
import json
import pytest
from jsonschema.validators import Draft202012Validator

from peerhub.core.models import (
    Peer,
    Stream,
    StreamState,
    Record,
    Offset,
    compute_payload_digest,
)

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "docs" / "m1_spec" / "04_SCHEMAS"


def test_peer_model_validates_against_json_schema():
    schema = json.loads((SCHEMA_DIR / "peer.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)

    peer = Peer(
        peer_id="peer-codex-1",
        display_name="Codex CLI Worker",
        adapter_ref="codex-cli",
        metadata={"capabilities": ["code", "test"]},
    )
    data = json.loads(peer.model_dump_json())
    validator.validate(data)
    assert data["peer_id"] == "peer-codex-1"
    assert data["schema_version"] == "1.0"


def test_stream_model_validates_against_json_schema():
    schema = json.loads((SCHEMA_DIR / "stream.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)

    stream = Stream(
        stream_id="stream-collab-42",
        title="Architecture Discussion",
        state=StreamState.OPEN,
        members=["peer-claude-1", "peer-codex-1"],
        revision=1,
    )
    data = json.loads(stream.model_dump_json())
    validator.validate(data)
    assert data["stream_id"] == "stream-collab-42"
    assert len(data["members"]) == 2


def test_record_model_validates_against_json_schema():
    schema = json.loads((SCHEMA_DIR / "record.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)

    digest = compute_payload_digest("message.user", {"text": "hello team"})
    record = Record(
        record_id="rec-001",
        stream_id="stream-collab-42",
        position=1,
        author_peer_id="peer-claude-1",
        kind="message.user",
        body={"text": "hello team"},
        targets=["peer-codex-1"],
        idempotency_key="idemp-msg-1",
        payload_digest=digest,
    )
    data = json.loads(record.model_dump_json())
    validator.validate(data)
    assert data["position"] == 1
    assert data["kind"] == "message.user"
    assert data["payload_digest"].startswith("sha256:")


def test_offset_model_validates_against_json_schema():
    schema = json.loads((SCHEMA_DIR / "offset.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)

    offset = Offset(
        peer_id="peer-codex-1",
        stream_id="stream-collab-42",
        read_through_position=10,
        revision=2,
    )
    data = json.loads(offset.model_dump_json())
    validator.validate(data)
    assert data["read_through_position"] == 10
    assert data["revision"] == 2
