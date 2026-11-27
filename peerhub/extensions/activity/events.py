"""Allowlisted projection of immutable source facts. Never materialize a Record body."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, cast

from peerhub.core.models import json_digest
from peerhub.extensions.ask_budgets import ASK_KEY_PREFIX, ask_delivery_tokens, ask_tokens
from peerhub.extensions.usage_normalization import PROVIDER_USAGE_CONTRACT, unknown_usage, validate_provider_usage

DETAIL_CAP = 16384
# Unknown evidence kinds have a stable identity, but their payload and user-defined name stay private.
EVENTS = frozenset("""
attempt_created about_to_invoke started session_call terminal session_telemetry response_appended
offset_acked caller launch_reserved execution_metadata output no_terminal runtime_error request_rejected
refusal_detected session_tainted prespawn_failure session_unavailable invocation_unresolved_recovered
reconcile_registered start_uncertain runtime_timeout runtime_disconnect processes_dead control_halt transition may_have_started late_terminal
""".split())
CERTAINTIES = {"NOT_STARTED", "MAY_HAVE_STARTED", "STARTED", "TERMINAL"}
REASONS = {"quota_exhausted", "rate_limited", "usage_limit", "invalid_model", "auth_failure", "incomplete", "truncated", "permission_denied"}
USAGE_REASONS = set("""
not_captured output_incomplete usage_absent no_allowlisted_counters invalid_usage invalid_output
missing_terminal malformed_output unknown_event unsupported_provider invalid_encoding event_too_large
stream_too_large duplicate_json_key invalid_event
""".split())


def obj(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, str):
        return {}
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return cast(dict[str, Any], value)
    except (ValueError, RecursionError):
        pass
    return {}


def time_text(value: Any) -> str | None:
    try:
        if isinstance(value, str):
            stamp = datetime.fromisoformat(value)
            if stamp.tzinfo is None:
                return None
        elif type(value) in (int, float):
            stamp = datetime.fromtimestamp(value, timezone.utc)
        else:
            return None
        return stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        return None


def event(channel: str, key: object, kind: str, row: sqlite3.Row, **facts: Any) -> dict[str, Any]:
    item = dict(source_event_id=f"{channel}:{key}", source_revision="1", kind=kind,
                stream_id=row["stream_id"], record_id=row["record_id"], **facts)
    item["source_digest"] = json_digest(item)
    return item


def record(row: sqlite3.Row) -> dict[str, Any]:
    name, key = row["kind"], row["request_key"]
    kind = {"prompt": "ai.prompt", "response": "ai.response", "control.reconcile": "peer.reconciliation"}.get(name, "peer.record")
    metadata = obj(row["metadata"])
    delivery = metadata.get("delivery_id") if name == "response" else None
    return event("record", row["record_id"], kind, row, position=row["position"], author_peer=row["author_peer_id"],
                 target_peer=row["target_peer"], reply_to=row["reply_to"], delivery_id=delivery if isinstance(delivery, str) else None,
                 request_id=key[len(ASK_KEY_PREFIX):] if name == "prompt" and key and key.startswith(ASK_KEY_PREFIX) else None,
                 output_truncated=metadata.get("output_truncated") is True or None,
                 occurred_at=time_text(row["created_at"]), content={"mode": "OMITTED", "reason": "METADATA_ONLY"})


def validated(detail: dict[str, Any], row: sqlite3.Row) -> dict[str, Any]:
    provider, raw = detail.get("provider_kind"), detail.get("usage")
    if (not isinstance(provider, str) or provider not in PROVIDER_USAGE_CONTRACT or not isinstance(raw, dict)
            or detail.get("schema_version") != 3 or not row["session_handle"]
            or detail.get("handle") != row["session_handle"] or type(detail.get("generation")) is not int
            or detail["generation"] != row["session_generation"]):
        return unknown_usage("unverified_receipt", receipt=True)
    source = cast(dict[str, Any], raw)
    candidate = {k: source.get(k) for k in ("schema_version", "scope", "source", "reported", "normalized", "coverage")}
    result = validate_provider_usage(candidate, provider)
    coverage = result["coverage"]
    if coverage["reason"] is not None and coverage["reason"] not in USAGE_REASONS:
        coverage["reason"] = "unverified_receipt"
    return result


def usage_facts(conn: sqlite3.Connection, row: sqlite3.Row, detail: dict[str, Any]) -> dict[str, Any]:
    usage = validated(detail, row)
    provider = detail.get("provider_kind")
    current = {"provider_kind": provider, "usage": usage}
    counts = ask_tokens(current)
    baseline: int | None = None
    if usage["scope"] == "session_cumulative" and usage["coverage"]["state"] == "MEASURED":
        # The immediately preceding delivery owns the baseline, including gaps. Never skip an
        # unmeasured attempt or read a receipt appended after this event.
        prior = conn.execute(
            "SELECT p.session_handle,p.session_generation,e.seq,CASE WHEN length(CAST(e.detail AS BLOB))<=? THEN e.detail END detail "
            "FROM bridge_deliveries p LEFT JOIN bridge_evidence e ON e.delivery_id=p.delivery_id "
            "AND e.kind='session_telemetry' AND e.seq<? WHERE p.session_handle=? AND p.stream_id=? "
            "AND p.peer_id=? AND p.seq<? ORDER BY p.seq DESC,e.seq DESC LIMIT 1",
            (DETAIL_CAP, row["seq"], row["session_handle"], row["stream_id"], row["peer_id"], row["delivery_seq"])).fetchone()
        if prior is None and row["session_mode"] == "fresh":
            counts = ask_delivery_tokens(conn, row["delivery_id"], current)
        elif prior is not None:
            previous = obj(prior["detail"])
            compatible = ("generation", "configured_binding", "configured_model", "resolved_model", "adapter_version", "provider_kind")
            if (isinstance(detail.get("configured_binding"), dict)
                    and all(previous.get(k) == detail.get(k) for k in compatible)):
                prev_usage = validated(previous, prior)
                if prev_usage["coverage"]["state"] == "MEASURED":
                    counts = ask_tokens(current, {"provider_kind": provider, "usage": prev_usage})
                    baseline = prior["seq"]
    return {"provider": provider if isinstance(provider, str) and provider in PROVIDER_USAGE_CONTRACT else None,
            "usage": usage, "delivery_measurement": counts, "baseline_evidence_seq": baseline,
            "measurement_state": "MEASURED" if any(n is not None for n in counts.values()) else "UNKNOWN"}


def evidence(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    name = row["event"]
    detail = obj(row["detail"])
    facts: dict[str, Any] = {"event": name if name in EVENTS else "unclassified",
                             "delivery_id": row["delivery_id"], "attempt": row["attempt"], "peer_id": row["peer_id"],
                             "certainty": row["certainty"] if row["certainty"] in CERTAINTIES else None,
                             "acked": True if name == "offset_acked" else None,
                             "occurred_at": time_text(detail.get("observed_at", detail.get("started_at")))}
    kind = "peer.evidence"
    if name in ("about_to_invoke", "started", "prespawn_failure", "invocation_unresolved_recovered"):
        facts["invocation_no"] = detail.get("invocation_no") if type(detail.get("invocation_no")) is int else None
    if name in ("session_call", "session_telemetry"):
        facts["session_handle"] = row["session_handle"] if detail.get("handle") == row["session_handle"] else None
        facts["session_generation"] = row["session_generation"] if detail.get("generation") == row["session_generation"] else None
    if name == "session_call":
        kind = "peer.session"
        facts["mode"] = detail.get("mode") if detail.get("mode") in ("fresh", "resumed") else None
    elif name == "session_telemetry":
        kind = "peer.usage"
        facts.update(usage_facts(conn, row, detail))
    elif name == "response_appended":
        facts["response_record_id"] = detail.get("record_id") if isinstance(detail.get("record_id"), str) else None
    elif name in ("attempt_created", "reconcile_registered"):
        facts["reconcile_record_id"] = detail.get("reconcile_record_id") if isinstance(detail.get("reconcile_record_id"), str) else None
    if name == "offset_acked":
        code = row["settled_reason"]
        facts["status"] = ("failed_not_started" if row["certainty"] == "NOT_STARTED" else
                           
                           "not_executed" if row["not_executed"] else code if code in ("incomplete", "truncated") else None)
        facts["reason"] = code if code in REASONS else None
    return event("evidence", row["seq"], kind, row, **facts)
