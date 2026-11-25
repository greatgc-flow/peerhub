"""Consume API billing evidence in the existing observation store."""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from peerhub.core.models import json_digest
from peerhub.core.peer_kinds import API_PEER_KINDS
from peerhub.extensions.observation import ObservationStore
from peerhub.extensions.observation_model import (
    BILLING_KINDS, EvidenceState, FreshnessPolicy, Observation, ObservationCorruptError,
    epoch_to_iso, evaluate, row_to_observation, valid_epoch_seconds,
)
from peerhub.extensions.runtime_contract import BillingScope as BillingScope, valid_billing_fingerprint
from peerhub.extensions.usage_normalization import PROVIDER_USAGE_CONTRACT

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


def record_amount(
    db_path: str | Path, scope: BillingScope, kind: MoneyKind, *, source: MoneySource, evidence_id: str,
    amount_micros: int, currency: str, observed_at: float,
) -> str:
    """Import a verified offline fact. The collector owns verification and provides a stable source event ID.

    The evidence ID is namespaced by provider, kind and source and must survive credential rotation. A conflicting
    replay raises ObservationExistsError, leaving the original fact intact.
    """
    if (kind not in ("balance", "estimate", "charge") or source not in MONEY_SOURCES[kind]
            or type(evidence_id) is not str or not evidence_id
            or type(currency) is not str or _CURRENCY.fullmatch(currency) is None
            or type(amount_micros) is not int or not -(2**63) <= amount_micros < 2**63
            or not valid_epoch_seconds(observed_at)):
        raise ValueError("invalid monetary billing evidence")
    obs_id = "billing-" + json_digest([scope.provider, kind, source, evidence_id], strict=False)
    obs = Observation(
        observation_id=obs_id, subject_ref=scope.provider, kind=f"billing_{kind}",
        source=source, observed_at=epoch_to_iso(observed_at), state=EvidenceState.MEASURED,
        payload={"provider": scope.provider, "account_scope": scope.fingerprint,
                 "amount_micros": amount_micros, "currency": currency},
    )
    ObservationStore(db_path, require_existing=True).persist_replayable(obs)
    return obs_id


def record_delivery(
    conn: sqlite3.Connection, delivery_id: str, observed_at: float, *, usage: dict[str, Any] | None = None,
    uncertain: bool = False,
) -> None:
    """Project a fenced outcome using the immutable scope captured before its invocation."""
    row = conn.execute(
        "SELECT peer_id,json_extract(detail,'$.invocation_no'),json_extract(detail,'$.billing_provider'),"
        "json_extract(detail,'$.billing_account_scope') FROM bridge_evidence "
        "WHERE delivery_id=? AND kind='about_to_invoke' AND json_valid(detail) ORDER BY seq DESC LIMIT 1",
        (delivery_id,),
    ).fetchone()
    if row is None:
        return
    peer, invocation, provider, scope = row
    if provider not in API_PEER_KINDS or type(invocation) is not int or invocation < 1 or not _valid_scope(scope):
        return  # a legacy delivery or a runtime with no bound invocation evidence
    obs_id = "billing-" + json_digest([delivery_id, invocation, "uncertain" if uncertain else "usage"], strict=False)
    if conn.execute("SELECT 1 FROM observations WHERE observation_id=?", (obs_id,)).fetchone():
        return  # retain the first timestamp even if recovery reports uncertainty again

    source = "bridge.api_execution"
    counts: dict[str, Any] = {}
    if usage is not None and usage.get("coverage", {}).get("state") == "MEASURED":
        counts = {key: usage["reported"][key] for key in ("input_tokens", "output_tokens", "total_tokens")
                  if key in usage["reported"]}
        source = PROVIDER_USAGE_CONTRACT[provider][1]
    reason = None
    if uncertain:
        reason = "execution_uncertain"
    elif not counts:
        reason = "usage_missing"
    elif not {"input_tokens", "output_tokens"} <= counts.keys():
        reason = "usage_partial"
    elif scope is None:
        reason = "account_scope_unverified"
    ObservationStore.append(conn, Observation(
        observation_id=obs_id, subject_ref=peer, kind="billing_usage", source=source, observed_at=epoch_to_iso(observed_at),
        state=EvidenceState.MEASURED if counts else EvidenceState.UNKNOWN,
        payload={"provider": provider, "account_scope": scope, "delivery_ref": json_digest(delivery_id, strict=False),
                 "invocation": invocation,
                 "status": "unreconciled" if reason else "usage_reported", "reason": reason, "unit": "token", **counts},
    ))


def _valid_scope(scope: object) -> bool:
    return scope is None or valid_billing_fingerprint(scope)


def billing_payload(kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the typed fact fields shared by the read-only views."""
    provider, scope = payload.get("provider"), payload.get("account_scope")
    if provider not in API_PEER_KINDS or not _valid_scope(scope):
        return {}
    result = {"provider": provider, "account_scope": scope}
    if kind == "billing_usage":
        reason = payload.get("reason")
        ref, invocation = payload.get("delivery_ref"), payload.get("invocation")
        if (not isinstance(ref, str) or _DELIVERY_REF.fullmatch(ref) is None
                or type(invocation) is not int or invocation < 1
                or (reason is not None and (not isinstance(reason, str) or reason not in _USAGE_REASONS))):
            return {}
        result.update(delivery_ref=ref, invocation=invocation, unit="token", reason=reason,
                      status="unreconciled" if reason else "usage_reported")
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            value = payload.get(key)
            if type(value) is int and 0 <= value < 2**63:
                result[key] = value
    else:
        currency, amount = payload.get("currency"), payload.get("amount_micros")
        if (scope is None or not isinstance(currency, str) or _CURRENCY.fullmatch(currency) is None
                or type(amount) is not int or not -(2**63) <= amount < 2**63):
            return {}
        result.update(currency=currency, amount_micros=amount)
    return result


def peer_billing(conn: sqlite3.Connection, peer: str, provider: str, read_at: float, *,
                 policy: FreshnessPolicy | None = None) -> dict[str, Any]:
    """Read a bounded sample of each billing kind from the caller's snapshot."""
    facts: list[dict[str, Any]] = []
    truncated = False
    status = "OK"
    freshness = policy or FreshnessPolicy()
    read_us = round(read_at * 1_000_000)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='observations' AND type='table'").fetchone():
        for kind in BILLING_KINDS:
            rows = conn.execute(
                "SELECT * FROM observations WHERE subject_ref=? AND kind=? "
                "ORDER BY effective_at_us DESC,capture_seq DESC LIMIT ?", (peer, kind, _PEER_SAMPLE + 1),
            ).fetchall()
            truncated |= len(rows) > _PEER_SAMPLE
            for row in rows[:_PEER_SAMPLE]:
                try:
                    obs, seq = row_to_observation(row)
                except ObservationCorruptError:
                    status = "ERROR"
                    continue
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
