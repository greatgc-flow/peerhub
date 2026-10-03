"""PeerHub M1 Very Simple Core Models.

Strictly adheres to JSON Schema Draft 2020-12 specifications in docs/m1_spec/04_SCHEMAS/.
Core invariant: Does NOT import any extension.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Mapping
from pydantic import BaseModel, ConfigDict, Field, field_validator


SCHEMA_VERSION: str = "1.0"


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


_RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$")


def is_rfc3339(value: str) -> bool:
    """Strict RFC 3339 date-time (matches schema `format: date-time`)."""
    if not isinstance(value, str) or not _RFC3339.match(value):
        return False
    try:
        datetime.fromisoformat(value.replace("z", "Z"))
    except ValueError:
        return False
    return True


def assert_json_value(value: Any, path: str = "$") -> None:
    """Reject anything that is not a canonical JSON value (SCH-014/015): NaN/Infinity, bytes, sets, objects, non-str keys."""
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite number at {path}")
        return
    if isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            assert_json_value(v, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(f"non-string key {k!r} at {path}")
            assert_json_value(v, f"{path}.{k}")
        return
    raise TypeError(f"non-JSON value {type(value).__name__} at {path}")


def canonical_json_bytes(obj: Any) -> bytes:
    """Produce deterministic UTF-8 canonical JSON bytes (RFC 8785 subset); strict: no NaN/Infinity."""
    assert_json_value(obj)
    return json.dumps(
        obj,
        allow_nan=False,
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


class _CoreModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("metadata", "created_at", "appended_at", check_fields=False, mode="before")
    @classmethod
    def _strict_common(cls, v: Any, info: Any) -> Any:
        if info.field_name == "metadata":
            assert_json_value(v, "metadata")
        elif not is_rfc3339(v):
            raise ValueError(f"{info.field_name} is not an RFC 3339 date-time: {v!r}")
        return v


class Peer(_CoreModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = Field(default="1.0", frozen=True)
    peer_id: str = Field(..., min_length=1)
    display_name: str | None = None
    adapter_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)


class Stream(_CoreModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = Field(default="1.0", frozen=True)
    stream_id: str = Field(..., min_length=1)
    title: str | None = None
    state: StreamState = StreamState.OPEN
    members: list[str] = Field(default_factory=list)
    revision: int = Field(default=1, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)

    @field_validator("members")
    @classmethod
    def _unique_members(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v) or any(not m for m in v):
            raise ValueError("members must be unique non-empty strings")
        return v


class Record(_CoreModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = Field(default="1.0", frozen=True)
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

    @field_validator("targets")
    @classmethod
    def _unique_targets(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v) or any(not t for t in v):
            raise ValueError("targets must be unique non-empty strings")
        return v

    @field_validator("refs")
    @classmethod
    def _nonempty_refs(cls, v: list[str]) -> list[str]:
        if any(not r for r in v):
            raise ValueError("refs must be non-empty strings")
        return v

    @field_validator("body", mode="before")
    @classmethod
    def _json_body(cls, v: Any) -> Any:
        assert_json_value(v, "body")
        return v


class Offset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = Field(default="1.0", frozen=True)
    peer_id: str = Field(..., min_length=1)
    stream_id: str = Field(..., min_length=1)
    read_through_position: int = Field(default=0, ge=0)
    revision: int = Field(default=1, ge=1)
