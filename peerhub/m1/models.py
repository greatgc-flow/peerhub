"""PeerHub M1 Very Simple Core Models.

Strictly adheres to JSON Schema Draft 2020-12 specifications in docs/m1_spec/04_SCHEMAS/.
Core invariant: Does NOT import any extension.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping
from pydantic import BaseModel, ConfigDict, Field


SCHEMA_VERSION: str = "1.0"


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def canonical_json_bytes(obj: Any) -> bytes:
    """Produce deterministic UTF-8 canonical JSON bytes (RFC 8785 subset)."""
    return json.dumps(
        obj,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def compute_payload_digest(kind: str, body: Any) -> str:
    """Compute sha256:<hex> digest over kind + body."""
    raw = canonical_json_bytes({"kind": kind, "body": body})
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


class StreamState(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class Peer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SCHEMA_VERSION, frozen=True)
    peer_id: str = Field(..., min_length=1)
    display_name: str | None = None
    adapter_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)


class Stream(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SCHEMA_VERSION, frozen=True)
    stream_id: str = Field(..., min_length=1)
    title: str | None = None
    state: StreamState = StreamState.OPEN
    members: list[str] = Field(default_factory=list)
    revision: int = Field(default=1, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SCHEMA_VERSION, frozen=True)
    record_id: str = Field(..., min_length=1)
    stream_id: str = Field(..., min_length=1)
    position: int = Field(..., ge=1)
    author_peer_id: str = Field(..., min_length=1)
    kind: str = Field(..., pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    body: Any = Field(default=None)
    targets: list[str] = Field(default_factory=list)
    reply_to: str | None = None
    refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(..., min_length=1)
    payload_digest: str = Field(..., pattern=r"^sha256:[0-9a-f]{64}$")
    created_at: str = Field(default_factory=utc_now_iso)
    appended_at: str = Field(default_factory=utc_now_iso)


class Offset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SCHEMA_VERSION, frozen=True)
    peer_id: str = Field(..., min_length=1)
    stream_id: str = Field(..., min_length=1)
    read_through_position: int = Field(default=0, ge=0)
    revision: int = Field(default=1, ge=1)
