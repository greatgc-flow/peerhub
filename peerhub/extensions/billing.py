"""Consume API billing evidence in the existing observation store."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypeGuard, cast

from peerhub.core.models import json_digest
from peerhub.core.peer_kinds import API_PEER_KINDS
from peerhub.extensions.billing_secret import store_secret
from peerhub.extensions.observation import ObservationStore
from peerhub.extensions.observation_model import (
    BILLING_KINDS, EvidenceState, FreshnessPolicy, Observation, ObservationCorruptError,
    epoch_to_iso, evaluate, row_to_observation, valid_epoch_seconds,
)
from peerhub.extensions.schema_guard import refuse_future_schema
from peerhub.extensions.usage_normalization import PROVIDER_USAGE_CONTRACT, REPORTED_FIELDS

MoneyKind = Literal["balance", "estimate", "charge"]
MoneySource = Literal["provider_console", "provider_statement", "collector_estimate"]
MONEY_SOURCES: Mapping[str, tuple[MoneySource, ...]] = {
    "balance": ("provider_console", "provider_statement"),
    "estimate": ("collector_estimate",),
    "charge": ("provider_statement",),
}
_CURRENCY = re.compile(r"[A-Z]{3}")
_DELIVERY_REF = re.compile(r"[0-9a-f]{64}")
_USAGE_REASONS = {"execution_uncertain", "usage_missing", "usage_partial", "account_scope_unverified"}
_PEER_SAMPLE = 5  # per kind
_MONEY_LIMIT = 1024  # per store, including replay IDs
_OLD_FINGERPRINT = re.compile(r"(?:key|account):[0-9a-f]{64}")
_FINGERPRINT = re.compile(r"v2:(?:key|account):[0-9a-f]{64}")
_MONEY_WHERE = "kind IN ('billing_balance','billing_estimate','billing_charge')"
_MONEY_INDEX = "idx_observation_billing_money_v2"


class BillingCapacityError(ValueError):
    """The store has no space for another distinct monetary observation."""


def valid_fingerprint(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and _FINGERPRINT.fullmatch(value) is not None


def _rekey(secret: bytes, legacy: str) -> str:
    # Key the old digest so existing scopes can be upgraded without the original identity.
    digest = hmac.digest(secret, b"peerhub.billing.scope.v2\x00" + legacy.encode("ascii"), "sha256")
    return f"v2:{legacy.split(':', 1)[0]}:{digest.hex()}"


def _fingerprint(secret: bytes, provider: str, identity: str, identity_kind: str) -> str:
    digest = hashlib.sha256(f"peerhub.billing.v1\x00{provider}\x00{identity_kind}\x00".encode())
    digest.update(identity.encode("utf-8", errors="surrogatepass"))
    return _rekey(secret, f"{identity_kind}:{digest.hexdigest()}")


def _store_tag(secret: bytes) -> bytes:
    return hmac.digest(secret, b"peerhub.billing.store.v2", "sha256")


def _has_schema_entry(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone() is not None


def _upgrade_fingerprints(conn: sqlite3.Connection, secret: bytes) -> None:
    """Change only the old credential digest fields, with the original guards restored before commit."""
    if _has_schema_entry(conn, _MONEY_INDEX):
        return
    conn.execute("PRAGMA secure_delete=ON")
    def key_legacy_scope(value: object) -> str | None:
        return _rekey(secret, value) if isinstance(value, str) and _OLD_FINGERPRINT.fullmatch(value) else None

    conn.create_function("key_billing_scope", 1, key_legacy_scope)
    for table, column, scope_path, kind_sql in (
        ("observations", "payload_json", "$.account_scope", "(kind='billing_usage' OR " + _MONEY_WHERE + ")"),
        ("bridge_evidence", "detail", "$.billing_account_scope", "kind='about_to_invoke'"),
    ):
        if not _has_schema_entry(conn, table):
            continue
        trigger = f"{table}_no_update"
        guard = conn.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (trigger,)).fetchone()
        if guard:
            conn.execute(f"DROP TRIGGER {trigger}")
        conn.execute(
            f"UPDATE {table} SET {column}=json_set({column}, ?, key_billing_scope(json_extract({column}, ?))) "
            f"WHERE {kind_sql} AND CASE WHEN json_valid({column}) "
            f"THEN key_billing_scope(json_extract({column}, ?)) IS NOT NULL END",
            (scope_path,) * 3,
        )
        if guard:
            conn.execute(guard[0])
    conn.execute(f"CREATE INDEX {_MONEY_INDEX} ON observations(kind) WHERE {_MONEY_WHERE}")


def _scope_key(db_path: str | Path) -> bytes:
    db = Path(db_path).resolve(strict=True)
    refuse_future_schema(db)
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
        ready = _has_schema_entry(conn, _MONEY_INDEX)
    if ready:
        return store_secret(db, create=False)
    with ObservationStore(db, require_existing=True).write_tx() as conn:
        secret = store_secret(db, create=not _has_schema_entry(conn, _MONEY_INDEX))
        _upgrade_fingerprints(conn, secret)
        return secret


@dataclass(frozen=True)
class BillingScope:
    provider: str
    fingerprint: str
    _tag: bytes = field(repr=False)

    @classmethod
    def from_verified_identity(
        cls, db_path: str | Path, provider: str, identity: str, *, identity_kind: Literal["key", "account"],
    ) -> BillingScope:
        if provider not in API_PEER_KINDS or identity_kind not in ("key", "account") or not identity:
            raise ValueError("invalid billing identity")
        secret = _scope_key(db_path)
        return cls(provider, _fingerprint(secret, provider, identity, identity_kind), _store_tag(secret))


def key_signer(db_path: str | Path, provider: str) -> Callable[[str], str | None]:
    def sign(key: str) -> str | None:
        try:
            secret = _scope_key(db_path)
        except (OSError, sqlite3.Error):
            return None  # An unavailable private file leaves billing scope unknown.
        return _fingerprint(secret, provider, key, "key")
    return sign


def record_amount(
    db_path: str | Path, scope: BillingScope, kind: MoneyKind, *, source: MoneySource, evidence_id: str,
    amount_micros: int, currency: str, observed_at: float,
) -> str:
    """Import a verified offline fact. The collector owns verification and provides a stable source event ID.

    The evidence ID is namespaced by provider, kind and source and must survive credential rotation. A conflicting
    replay raises ObservationExistsError, leaving the original fact intact.
    """
    if (kind not in ("balance", "estimate", "charge") or source not in MONEY_SOURCES[kind]
            or scope.provider not in API_PEER_KINDS or not valid_fingerprint(scope.fingerprint)
            or type(evidence_id) is not str or not evidence_id
            or type(currency) is not str or _CURRENCY.fullmatch(currency) is None
            or type(amount_micros) is not int or not -(2**63) <= amount_micros < 2**63
            or not valid_epoch_seconds(observed_at)):
        raise ValueError("invalid monetary billing evidence")
    if not hmac.compare_digest(scope._tag, _store_tag(_scope_key(db_path))):  # pyright: ignore[reportPrivateUsage]
        raise ValueError("billing account scope belongs to a different store")
    obs_id = "billing-" + json_digest([scope.provider, kind, source, evidence_id], strict=False)
    obs = Observation(
        observation_id=obs_id, subject_ref=scope.provider, kind=f"billing_{kind}",
        source=source, observed_at=epoch_to_iso(observed_at), state=EvidenceState.MEASURED,
        payload={"provider": scope.provider, "account_scope": scope.fingerprint,
                 "amount_micros": amount_micros, "currency": currency},
    )
    with ObservationStore(db_path, require_existing=True).write_tx() as conn:
        _append_money(conn, obs)
    return obs_id


def _append_money(conn: sqlite3.Connection, obs: Observation) -> None:
    if (not conn.execute("SELECT 1 FROM observations WHERE observation_id=?", (obs.observation_id,)).fetchone()
            and conn.execute(f"SELECT COUNT(*) FROM observations WHERE {_MONEY_WHERE}").fetchone()[0] >= _MONEY_LIMIT):
        raise BillingCapacityError("billing monetary observation capacity reached")
    ObservationStore.append(conn, obs, replayable=True)


def _delivery_observation(peer: str, provider: str, delivery_id: str, invocation: int, scope: object,
                          observed_at: float, receipt: Mapping[str, Any], *, uncertain: bool) -> Observation:
    obs_id = "billing-" + json_digest([delivery_id, invocation, "uncertain" if uncertain else "usage"], strict=False)
    source = "bridge.api_execution"
    usage = _object(receipt.get("usage"))
    candidates = _object(usage.get("reported")) if _object(usage.get("coverage")).get("state") == "MEASURED" else {}
    counts = {key: value for key in REPORTED_FIELDS[provider]
              if type(value := candidates.get(key)) is int and 0 <= value < 2**63}
    if counts:
        source = PROVIDER_USAGE_CONTRACT[provider][1]
    reason = None
    if uncertain:
        reason = "execution_uncertain"
    elif not counts:
        reason = "usage_missing"
    elif not {"input_tokens", "output_tokens"} <= counts.keys():
        reason = "usage_partial"
    elif not valid_fingerprint(scope):
        reason = "account_scope_unverified"
    return Observation(
        observation_id=obs_id, subject_ref=peer, kind="billing_usage", source=source, observed_at=epoch_to_iso(observed_at),
        state=EvidenceState.MEASURED if counts else EvidenceState.UNKNOWN,
        payload={"provider": provider, "account_scope": scope if valid_fingerprint(scope) else None,
                 "delivery_ref": json_digest(delivery_id, strict=False),
                 "invocation": invocation,
                 "status": "unreconciled" if reason else "usage_reported", "reason": reason, "unit": "token", **counts},
    )


def billing_payload(kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the typed fact fields shared by the read-only views."""
    provider, scope = payload.get("provider"), payload.get("account_scope")
    if provider not in API_PEER_KINDS:
        return {}
    if not valid_fingerprint(scope):
        scope = None
    result: dict[str, Any] = {"provider": provider, "account_scope": scope}
    if kind == "billing_usage":
        reason = payload.get("reason")
        ref, invocation = payload.get("delivery_ref"), payload.get("invocation")
        if (not isinstance(ref, str) or _DELIVERY_REF.fullmatch(ref) is None
                or type(invocation) is not int or invocation < 1
                or (reason is not None and (not isinstance(reason, str) or reason not in _USAGE_REASONS))):
            return {}
        if scope is None and reason is None:
            reason = "account_scope_unverified"
        result.update(delivery_ref=ref, invocation=invocation, unit="token", reason=reason,
                      status="unreconciled" if reason else "usage_reported")
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            value = payload.get(key)
            if type(value) is int and 0 <= value < 2**63:
                result[key] = value
    else:
        currency, amount = payload.get("currency"), payload.get("amount_micros")
        if (not isinstance(currency, str) or _CURRENCY.fullmatch(currency) is None
                or type(amount) is not int or not -(2**63) <= amount < 2**63):
            return {}
        result.update(currency=currency, amount_micros=amount)
    return result


def _object(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _json_dict(raw: str | None) -> dict[str, Any]:
    try:
        return _object(json.loads(raw)) if raw is not None else {}
    except (ValueError, RecursionError):
        return {}


def _delivery_facts(conn: sqlite3.Connection, peer: str, provider: str) -> list[tuple[Observation, int]]:
    if not _has_schema_entry(conn, "bridge_deliveries"):
        return []
    # The latest invocation carries the bound scope. The outcome uses its
    # existing Bridge evidence, so uncertain usage survives a bounded billing view.
    rows = conn.execute(
        "SELECT d.delivery_id, d.result_json IS NULL, m.detail, e.detail, m.seq "
        "FROM bridge_deliveries d JOIN bridge_evidence m ON m.seq=(SELECT seq FROM bridge_evidence "
        " WHERE delivery_id=d.delivery_id AND kind='about_to_invoke' ORDER BY seq DESC LIMIT 1) "
        "LEFT JOIN bridge_evidence e ON e.seq=(SELECT seq FROM bridge_evidence WHERE delivery_id=d.delivery_id AND seq>m.seq "
        " AND kind IN ('session_telemetry','runtime_error','no_terminal','start_uncertain',"
        " 'invocation_unresolved_recovered','processes_dead') "
        " ORDER BY CASE kind WHEN 'session_telemetry' THEN 0 WHEN 'runtime_error' THEN 1 ELSE 2 END, seq LIMIT 1) "
        "WHERE d.peer_id=? AND d.certainty IN ('MAY_HAVE_STARTED','STARTED','TERMINAL') "
        "AND CASE WHEN json_valid(m.detail) THEN json_extract(m.detail,'$.billing_provider') END=? "
        "AND (d.certainty='TERMINAL' OR CASE WHEN json_valid(d.result_json) "
        " THEN json_extract(d.result_json,'$.reason') IN ('incomplete','truncated') "
        " ELSE d.result_json IS NULL AND NOT EXISTS (SELECT 1 FROM bridge_evidence "
        " WHERE delivery_id=d.delivery_id AND seq>m.seq AND kind='refusal_detected') END) "
        "ORDER BY d.seq DESC LIMIT ?", (peer, provider, _PEER_SAMPLE + 1),
    )
    facts: list[tuple[Observation, int]] = []
    for delivery, uncertain, raw_marker, raw_outcome, seq in rows:
        marker = _json_dict(raw_marker)
        outcome = _json_dict(raw_outcome)
        at = outcome.get("observed_at", outcome.get("observed_at_s", marker.get("quota_invoked_at")))
        invocation = marker.get("invocation_no")
        if type(invocation) is int and invocation > 0 and isinstance(at, (int, float)) and valid_epoch_seconds(at):
            obs = _delivery_observation(peer, provider, delivery, invocation, marker.get("billing_account_scope"),
                                        at, outcome, uncertain=bool(uncertain))
            facts.append((obs, seq))
    return facts


def peer_billing(conn: sqlite3.Connection, peer: str, provider: str, read_at: float, *,
                 policy: FreshnessPolicy | None = None) -> dict[str, Any]:
    """Read a bounded sample of each billing kind from the caller's snapshot."""
    facts: list[dict[str, Any]] = []
    truncated = False
    status = "OK"
    freshness = policy or FreshnessPolicy()
    read_us = round(read_at * 1_000_000)
    has_observations = _has_schema_entry(conn, "observations")
    for kind in BILLING_KINDS:
        observations = dict[str, tuple[Observation, int]]()
        if kind == "billing_usage":
            observations.update((obs.observation_id, (obs, seq)) for obs, seq in _delivery_facts(conn, peer, provider))
        if has_observations:
            subject = peer if kind == "billing_usage" else provider
            rows = conn.execute(
                "SELECT * FROM observations WHERE subject_ref=? AND kind=? "
                "ORDER BY effective_at_us DESC,capture_seq DESC LIMIT ?", (subject, kind, _PEER_SAMPLE + 1),
            ).fetchall()
            for row in rows:
                try:
                    obs, seq = row_to_observation(row)
                except ObservationCorruptError:
                    status = "ERROR"
                    continue
                observations[obs.observation_id] = obs, seq  # prefer a legacy observation's original timestamp
        truncated |= len(observations) > _PEER_SAMPLE
        for obs, seq in sorted(observations.values(), key=lambda item: (item[0].observed_at, item[1]), reverse=True)[:_PEER_SAMPLE]:
            payload = billing_payload(kind, obs.payload)
            sources = (("bridge.api_execution", PROVIDER_USAGE_CONTRACT[provider][1]) if kind == "billing_usage"
                       else MONEY_SOURCES[kind.removeprefix("billing_")])
            if payload.get("provider") != provider or obs.source not in sources:
                status = "ERROR"
                continue
            view = evaluate(obs, seq, read_us, freshness)
            facts.append(dict(observation_id=obs.observation_id, kind=kind, source=obs.source,
                              observed_at=obs.observed_at, state=view.state.value, stored_state=obs.state.value,
                              age_seconds=view.age_seconds, ttl_seconds=view.ttl_seconds, **payload))
    return {"observations": facts, "truncated": truncated, "status": status}
