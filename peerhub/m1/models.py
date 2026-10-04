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
from typing import Any, Literal, Mapping, Sequence, cast
from pydantic import BaseModel, ConfigDict, Field, field_validator


SCHEMA_VERSION: str = "1.0"


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


_RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$")


def is_rfc3339(value: str) -> bool:
    """Strict RFC 3339 date-time (matches schema `format: date-time`)."""
    v = cast(object, value)  # wire/untyped boundary: the runtime type check is intentional
    if not isinstance(v, str) or not _RFC3339.match(v):
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
    if isinstance(value, list):  # TD-24: tuples/other containers are rejected, never coerced
        for i, v in enumerate(cast("list[Any]", value)):
            assert_json_value(v, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for k, v in cast("dict[Any, Any]", value).items():
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
    """Canonicalization primitive over kind + body. NOT the Record digest (see compute_record_digest, TD-02)."""
    raw = canonical_json_bytes({"kind": kind, "body": body})
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


SEMANTIC_FIELDS = ("stream_id", "author_peer_id", "kind", "body", "targets", "reply_to", "refs", "metadata", "created_at")
SERVER_OWNED_FIELDS = ("record_id", "position", "payload_digest", "appended_at")


def compute_record_digest(fields: Mapping[str, Any]) -> str:
    """TD-02: sha256 over canonical JSON of the client semantic projection (+schema_version).

    Server-owned fields (record_id/position/payload_digest/appended_at) are never read. Absent optional
    fields project as null/[]/{}; created_at is required (D-W1-2) so the stored Record recomputes to its digest.
    """
    proj = {
        "stream_id": fields["stream_id"],
        "author_peer_id": fields["author_peer_id"],
        "kind": fields["kind"],
        "body": fields.get("body"),
        "targets": list(fields.get("targets") or []),
        "reply_to": fields.get("reply_to"),
        "refs": list(fields.get("refs") or []),
        "metadata": dict(fields.get("metadata") or {}),
        "created_at": fields["created_at"],
        "schema_version": SCHEMA_VERSION,
    }
    return f"sha256:{hashlib.sha256(canonical_json_bytes(proj)).hexdigest()}"


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
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: Literal["1.0"] = Field(default="1.0", frozen=True)
    peer_id: str = Field(..., min_length=1)
    display_name: str | None = None
    adapter_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)


class Stream(_CoreModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

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
    model_config = ConfigDict(extra="forbid", frozen=True)  # CORE-004: immutable after append

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


class AppendRequest(BaseModel):
    """Client append request (TD-21): server-owned fields are forbidden, not ignored."""

    model_config = ConfigDict(extra="forbid")

    stream_id: str = Field(..., min_length=1)
    author_peer_id: str = Field(..., min_length=1)
    kind: str = Field(..., pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    body: Any = None
    idempotency_key: str = Field(..., min_length=1)
    targets: list[str] = Field(default_factory=list)
    reply_to: str | None = None
    refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str  # required by record.schema.json (D-W1-2)

    @field_validator("body", mode="before")
    @classmethod
    def _json_body(cls, v: Any) -> Any:
        assert_json_value(v, "body")
        return v

    @field_validator("metadata", mode="before")
    @classmethod
    def _json_meta(cls, v: Any) -> Any:
        assert_json_value(v, "metadata")
        return v

    @field_validator("targets", "refs", mode="before")
    @classmethod
    def _str_lists(cls, v: Any) -> Any:
        assert_json_value(v, "list")
        if not isinstance(v, (list, tuple)):
            raise ValueError("must be a list of non-empty strings")
        items = list(cast("Sequence[Any]", v))
        if any(not isinstance(x, str) or not x for x in items):
            raise ValueError("must be a list of non-empty strings")
        return items

    @field_validator("targets")
    @classmethod
    def _unique_targets(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("targets must be unique")
        return v

    @field_validator("created_at")
    @classmethod
    def _created_at(cls, v: str) -> str:
        if not is_rfc3339(v):
            raise ValueError(f"created_at is not an RFC 3339 date-time: {v!r}")
        return v


class Offset(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: Literal["1.0"] = Field(default="1.0", frozen=True)
    peer_id: str = Field(..., min_length=1)
    stream_id: str = Field(..., min_length=1)
    read_through_position: int = Field(default=0, ge=0)
    revision: int = Field(default=1, ge=1)
