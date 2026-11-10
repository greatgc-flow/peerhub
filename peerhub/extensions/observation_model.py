"""Observation / Resource Pool pure models, wire parsing and read-time freshness evaluation (no store, no writers).

Shared by the Observation store (peerhub.extensions.observation) and the read-only Diag (peerhub.extensions.diag).
Spec: OBSERVATION_AND_DIAG.md, PEER_CARDINALITY_RESOURCE_POOL.md, TD-13 (freshness clock), TD-23 (equal-time ordering).
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, cast

from jsonschema import FormatChecker
from jsonschema.validators import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from peerhub.core.models import assert_json_value, is_rfc3339
from peerhub.core.wire import WireValidationError

_SCHEMAS = Path(__file__).parent / "schemas"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_US = timedelta(microseconds=1)

KNOWN_KINDS = ("reachability", "cli_version", "runtime_capability", "session", "quota", "rate_limit", "activity", "execution_failure", "reset_credit")
# Measurement-bearing payload keys: only a MEASURED observation may carry them (honesty rule: unmeasured != 0/healthy/unlimited).
MEASUREMENT_KEYS = frozenset({"remaining", "remaining_fraction", "remaining_tokens", "remaining_requests", "used", "limit",
                              "requests_per_minute", "tokens_per_minute", "reset_in_seconds", "retry_after_seconds"})
CONDITION_STATES = frozenset({"UNAVAILABLE", "ERROR"})  # must identify the source condition
# An ABSENT pool row whose reason starts with this was recorded from a complete provider response that
# omitted the window (e.g. a plan without a 5h window); it ages like a measurement (see `evaluate`).
NOT_REPORTED_REASON = "not reported by provider"
# An ABSENT pool row recorded because the provider flagged the window disabled (e.g. the 5h limit while the weekly limit is hit);
# the provider description follows after ": ". It is not-applicable evidence and ages like NOT_REPORTED_REASON.
DISABLED_REASON = "disabled by provider"
ABSENT_FACT_REASONS = (NOT_REPORTED_REASON, DISABLED_REASON)


class EvidenceState(str, Enum):
    MEASURED = "MEASURED"
    ABSENT = "ABSENT"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class ObservationError(Exception):
    """Base class for Observation errors; the failed operation changed nothing."""


class InvalidObservationError(ObservationError, ValueError):
    """Evidence violates the honesty rules (e.g. a measurement attached to ABSENT/UNKNOWN)."""


class KindSemanticError(ObservationError, ValueError):
    """The source reported a different semantic than the requested kind (e.g. a rate limit offered as quota)."""


class UnknownResourcePoolError(ObservationError, LookupError):
    """resource_pool_ref does not name a registered Resource Pool."""


class ObservationExistsError(ObservationError, ValueError):
    """observation_id already exists: Observations are immutable evidence (refresh = new identity)."""


class PoolConflictError(ObservationError, ValueError):
    """Resource Pool already registered with different content (pools are immutable)."""


class ObservationCorruptError(ObservationError):
    """A stored Observation row cannot be parsed."""


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    observation_id: str = Field(..., min_length=1)
    subject_ref: str = Field(..., min_length=1)
    resource_pool_ref: str | None = None
    kind: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1)
    observed_at: str
    captured_at: str | None = None
    state: EvidenceState
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _times_and_payload(self) -> "Observation":
        if not is_rfc3339(self.observed_at):
            raise ValueError(f"observed_at: not an RFC 3339 date-time: {self.observed_at!r}")
        if self.captured_at is not None and not is_rfc3339(self.captured_at):
            raise ValueError(f"captured_at: not an RFC 3339 date-time: {self.captured_at!r}")
        assert_json_value(self.payload, "$.payload")
        return self


class ResourcePool(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    resource_pool_id: str = Field(..., min_length=1)
    provider: str = Field(..., min_length=1)
    kind: Literal["QUOTA", "RATE_LIMIT", "ACCOUNT", "RUNTIME"]
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class SourceReading:
    """What a source adapter's `probe()` returns. `semantic` is the semantic the source itself claims (quota / rate_limit / ...)."""

    state: EvidenceState
    payload: dict[str, Any] = field(default_factory=dict[str, Any])
    observed_at: str | None = None
    semantic: str | None = None


ACCOUNT_TTL_SECONDS = 6 * 3600.0


MAX_EPOCH_SECONDS = 253402300799  # 9999-12-31T23:59:59Z: the last instant every datetime/strftime path can represent


def valid_epoch_seconds(value: object) -> bool:
    """A real UTC instant between 1970-01-01T00:00:01Z and 9999-12-31T23:59:59Z (so formatting it can never raise)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):  # bounded as an int first: math.isfinite(10**309) would raise OverflowError
        return 1 <= value <= MAX_EPOCH_SECONDS
    return isinstance(value, float) and math.isfinite(value) and 1 <= value <= MAX_EPOCH_SECONDS


def account_text(value: object, limit: int = 24) -> str | None:
    """Display-only account label (plan, auth method, probe reason): printable ASCII without field delimiters, bounded.
    Used when normalizing a provider answer and again when reading stored values back."""
    text = re.sub(r"[^A-Za-z0-9 .+_-]", "", value) if isinstance(value, str) else ""
    return " ".join(text.split())[:limit].strip() or None


@dataclass(frozen=True)
class FreshnessPolicy:
    """TTL policy per observation kind (TD-13: age >= TTL is STALE)."""

    default_ttl_seconds: float = 300.0
    by_kind: Mapping[str, float] = field(default_factory=dict[str, float])

    def __post_init__(self) -> None:
        ttls: list[object] = [self.default_ttl_seconds, *self.by_kind.values()]  # untyped-boundary values: validated at runtime
        for v in ttls:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not v > 0:
                raise ValueError(f"TTL must be a positive number, got {v!r}")

    def ttl_for(self, kind: str) -> float:
        # Login/plan facts change over days, not minutes: an explicit by_kind entry wins, else accounts get ACCOUNT_TTL_SECONDS.
        return float(self.by_kind.get(kind, ACCOUNT_TTL_SECONDS if kind == "account" else self.default_ttl_seconds))


@dataclass(frozen=True)
class ObservationView:
    """Read-time view; the stored evidence is never rewritten. `skew_seconds` = observed_at - captured_at (retained source-clock skew)."""

    observation: Observation
    capture_seq: int
    stored_state: EvidenceState
    state: EvidenceState
    age_seconds: float | None
    ttl_seconds: float
    basis: str
    skew_seconds: float | None


# ----------------------------------------------------------------------------- time helpers
def instant_us(ts: str) -> int:
    """RFC 3339 instant -> integer microseconds since the epoch (exact, UTC)."""
    dt = datetime.fromisoformat(ts.replace("z", "Z"))
    return (dt.astimezone(timezone.utc) - _EPOCH) // _US


def epoch_to_iso(seconds: float) -> str:
    return (_EPOCH + timedelta(microseconds=round(seconds * 1_000_000))).isoformat(timespec="microseconds").replace("+00:00", "Z")


def effective_us(obs: Observation) -> int:
    """TD-13/TD-23: local captured_at is the freshness/latest clock when present, else observed_at."""
    return instant_us(obs.captured_at if obs.captured_at is not None else obs.observed_at)


# ----------------------------------------------------------------------------- honesty + freshness
def check_honesty(obs: Observation) -> None:
    if obs.state != EvidenceState.MEASURED:
        bad = sorted(MEASUREMENT_KEYS & set(obs.payload))
        if bad:
            raise InvalidObservationError(f"state {obs.state.value} must not carry measurement field(s) {bad}")
    if obs.state.value in CONDITION_STATES:
        cond = obs.payload.get("condition")
        if not isinstance(cond, str) or not cond:
            raise InvalidObservationError(f"state {obs.state.value} must identify the source condition (payload.condition)")


def evaluate(obs: Observation, capture_seq: int, read_us: int, policy: FreshnessPolicy) -> ObservationView:
    ttl = policy.ttl_for(obs.kind)
    basis = "captured_at" if obs.captured_at is not None else "observed_at"
    age_us = read_us - effective_us(obs)
    skew = (instant_us(obs.observed_at) - instant_us(obs.captured_at)) / 1e6 if obs.captured_at is not None else None
    age = age_us / 1e6 if age_us >= 0 else None  # a negative age is never reported as freshness
    if obs.state != EvidenceState.MEASURED:
        state = obs.state  # evidence is reported as stored; freshness never upgrades it
        if (obs.state == EvidenceState.ABSENT and age_us >= round(ttl * 1_000_000)
                and str(obs.payload.get("reason") or "").startswith(ABSENT_FACT_REASONS)):
            state = EvidenceState.STALE  # a "not reported" fact must be re-probed, never read as "no limit" forever
    elif age_us < 0:
        state = EvidenceState.UNKNOWN  # source/reader clocks disagree: no synthetic freshness
    elif age_us >= round(ttl * 1_000_000):
        state = EvidenceState.STALE
    else:
        state = EvidenceState.MEASURED
    return ObservationView(obs, capture_seq, obs.state, state, age, ttl, basis, skew)


COLUMNS = ("capture_seq", "observation_id", "subject_ref", "resource_pool_ref", "kind", "source", "state", "payload_json", "observed_at", "captured_at")


def row_to_observation(row: Any) -> tuple[Observation, int]:
    """Parse one stored row (mapping by COLUMNS); raises ObservationCorruptError when it is not valid evidence."""
    try:
        payload: object = json.loads(row["payload_json"])
        if not isinstance(payload, dict):
            raise ValueError("payload is not a JSON object")
        payload = cast("dict[str, Any]", payload)
        obs = Observation(observation_id=row["observation_id"], subject_ref=row["subject_ref"], resource_pool_ref=row["resource_pool_ref"],
                          kind=row["kind"], source=row["source"], state=EvidenceState(row["state"]), payload=payload,
                          observed_at=row["observed_at"], captured_at=row["captured_at"])
        check_honesty(obs)
    except (ValueError, TypeError, ValidationError, KeyError) as e:  # includes pydantic + honesty errors
        raise ObservationCorruptError(f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}") from e
    return obs, int(row["capture_seq"])


# ----------------------------------------------------------------------------- wire boundary
_FORMATS = FormatChecker()
def _check_date_time(v: object) -> bool:
    return is_rfc3339(v) if isinstance(v, str) else True


_FORMATS.checks("date-time")(_check_date_time)
_MODELS = {"peer-observation": Observation, "resource-pool": ResourcePool}


@lru_cache(maxsize=None)
def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((_SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8")), format_checker=_FORMATS)


def _parse(name: str, obj: Any) -> Any:
    errs = sorted(_validator(name).iter_errors(obj), key=lambda e: list(e.path))  # pyright: ignore[reportUnknownMemberType]  # jsonschema stubs leave iter_errors partially unknown
    if errs:
        raise WireValidationError("; ".join(f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errs[:5]))
    try:
        return _MODELS[name].model_validate(obj)
    except ValidationError as e:  # e.g. captured_at (plain string on the wire, RFC 3339 by model contract)
        first = e.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        msg = str(first["msg"]).removeprefix("Value error, ")
        raise WireValidationError(f"{loc}: {msg}" if loc else msg) from e


def parse_observation_wire(obj: Any) -> Observation:
    return _parse("peer-observation", obj)


def parse_resource_pool_wire(obj: Any) -> ResourcePool:
    return _parse("resource-pool", obj)


# ----------------------------------------------------------------------------- runtime read-only enforcement
_READ_ACTIONS = frozenset({sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE})


def read_only_authorizer(allow_transactions: bool = False) -> Callable[[int, str | None, str | None, str | None, str | None], int]:
    """sqlite3 authorizer allowing only SELECT/READ/FUNCTION/RECURSIVE (+ BEGIN/ROLLBACK, + the read form of PRAGMA query_only).
    Everything else (INSERT/UPDATE/DELETE/CREATE*/DROP*/ALTER/ATTACH/TEMP objects/PRAGMA writes/COMMIT) is denied at statement
    preparation time, whatever way the SQL text was built (static scanning can never be complete)."""
    def authorize(action: int, arg1: str | None, arg2: str | None, _db: str | None, _src: str | None) -> int:
        if action in _READ_ACTIONS:
            return sqlite3.SQLITE_OK
        if allow_transactions and action == sqlite3.SQLITE_TRANSACTION and arg1 in ("BEGIN", "ROLLBACK"):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and arg1 in ("query_only", "quick_check", "user_version") and arg2 is None:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    return authorize
