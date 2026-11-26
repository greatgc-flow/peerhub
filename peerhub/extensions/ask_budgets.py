"""Per-ask token budgets, outcome schema and quota-delta attribution."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from peerhub.core.models import iso_timestamp
from peerhub.core.peer_kinds import CLI_PEER_ALIASES
from peerhub.extensions.session_receipts import json_object, valid_observed_at


ASK_METRIC_DDL = (
    """CREATE TABLE IF NOT EXISTS bridge_ask_outcomes (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, record_id TEXT NOT NULL REFERENCES records(record_id),
        outcome TEXT NOT NULL CHECK(outcome IN ('accepted','rejected','retried')), marked_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_ask_outcome_latest ON bridge_ask_outcomes(record_id,seq DESC)",
    """CREATE TRIGGER IF NOT EXISTS bridge_ask_outcomes_no_update BEFORE UPDATE ON bridge_ask_outcomes
        BEGIN SELECT RAISE(ABORT,'ask outcomes are append only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_ask_outcomes_no_delete BEFORE DELETE ON bridge_ask_outcomes
        BEGIN SELECT RAISE(ABORT,'ask outcomes are append only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_ask_outcomes_no_replace BEFORE INSERT ON bridge_ask_outcomes
        WHEN EXISTS(SELECT 1 FROM bridge_ask_outcomes WHERE seq=NEW.seq)
        BEGIN SELECT RAISE(ABORT,'ask outcomes are append only'); END""",
    """CREATE TABLE IF NOT EXISTS bridge_ask_pool_deltas (
        delivery_id TEXT PRIMARY KEY REFERENCES bridge_deliveries(delivery_id), pool_id TEXT NOT NULL,
        before_id TEXT NOT NULL, after_id TEXT NOT NULL,
        delta_pp REAL NOT NULL CHECK(delta_pp BETWEEN 0 AND 100))""",
)

# The prompt Record is the ask identity. An idempotent replay or a reconciled delivery
# never adds another ask to this cohort.
ASK_KEY_PREFIX = "ask:"
ASK_RECORD = (f"r.kind='prompt' AND r.idempotency_key GLOB '{ASK_KEY_PREFIX}*' "
              "AND json_type(r.metadata_json,'$.binding')='text' AND json_type(r.metadata_json,'$.runtime_kind')='text'")

# Legacy prompts can have non-text metadata values. Leave unclassified JSON
# for the Python guard, which also preserves the original mismatch messages.
_STREAM_PROMPT = (
    f"kind='prompt' AND idempotency_key GLOB '{ASK_KEY_PREFIX}*' AND "
    "CASE WHEN json_valid(metadata_json) AND json_valid(targets_json) THEN "
    "json_type(metadata_json,'$.binding') IS NOT NULL AND json_type(metadata_json,'$.runtime_kind') IS NOT NULL "
    "AND json_array_length(targets_json)>0 ELSE 1 END"
)
_STREAM_TARGET = (
    "CASE WHEN json_valid(targets_json) THEN CASE WHEN json_array_length(targets_json)=1 "
    r"AND json_type(targets_json,'$[0]')='text' AND instr(targets_json,'\u0000')=0 "
    "THEN json_extract(targets_json,'$[0]') END END"
)
STREAM_PEER_CONFLICTS_SQL = " UNION ALL ".join(
    f"SELECT targets_json,metadata_json,position FROM records WHERE stream_id=:stream AND {_STREAM_PROMPT} AND {_STREAM_TARGET} {bound}"
    for bound in ("IS NULL", "<:peer", ">:peer")
) + " ORDER BY position"

# Only well-formed, whole-second UTC timestamps can be ordered lexically.
# Unknown and legacy ends take their own index range and retain the Python fallback.
_CONFIRMED_END = (
    "CASE WHEN length(CAST(terminal_at AS BLOB))=20 "
    "AND terminal_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z' "
    "AND substr(terminal_at,1,4)<>'0000' AND substr(terminal_at,12,2)<'24' "
    "AND date(substr(terminal_at,1,10),'+0 days')=substr(terminal_at,1,10) THEN terminal_at END"
)
WRITE_PATH_INDEXES = (
    f"CREATE INDEX IF NOT EXISTS idx_records_ask_target ON records(stream_id,{_STREAM_TARGET},position) WHERE {_STREAM_PROMPT}",
    f"CREATE INDEX IF NOT EXISTS idx_bd_confirmed_end ON bridge_deliveries({_CONFIRMED_END})",
)


def install_ask_metrics(conn: sqlite3.Connection) -> None:
    for stmt in ASK_METRIC_DDL:
        conn.execute(stmt)


def _token_counts(telemetry: Mapping[str, Any]) -> dict[str, int | None]:
    usage = telemetry.get("usage", {})
    normalized: dict[str, int] = usage.get("normalized", {}) if usage.get("coverage", {}).get("state") == "MEASURED" else {}
    cc = telemetry.get("provider_kind") == "cc"
    counts = {
        "input_tokens": normalized.get("uncached_input_tokens" if cc else "provider_input_tokens"),
        "output_tokens": normalized.get("output_tokens"),
        "cache_read_tokens": normalized.get("cache_read_input_tokens" if cc else "cached_input_tokens"),
        "cache_write_tokens": normalized.get("cache_creation_input_tokens" if cc else "cache_write_tokens"),
        # Normalization sums CC's three reported prompt counters. Other providers'
        # input already includes cached input, so adding it again would double count.
        "prompt_tokens": normalized.get("provider_input_tokens"),
    }
    return {key: value if type(value) is int and value >= 0 else None for key, value in counts.items()}


def ask_tokens(
    telemetry: Mapping[str, Any], previous: Mapping[str, Any] | None = None,
) -> dict[str, int | None]:
    """Convert measured delivery evidence into counters attributable to one ask."""
    scope = telemetry.get("usage", {}).get("scope")
    counts = _token_counts(telemetry)
    if scope == "delivery":
        return counts
    if (scope != "session_cumulative" or previous is None
            or previous.get("usage", {}).get("scope") != scope
            or previous.get("provider_kind") != telemetry.get("provider_kind")):
        return dict.fromkeys(counts)
    baseline = _token_counts(previous)
    deltas: dict[str, int | None] = {}
    for key, value in counts.items():
        prior = baseline[key]
        deltas[key] = value - prior if value is not None and prior is not None and value >= prior else None
    if telemetry.get("provider_kind") == "cc":
        # A reset in a component can be masked by growth in the total prompt counter.
        if any(deltas[key] is None for key in ("input_tokens", "cache_read_tokens", "cache_write_tokens")):
            deltas["prompt_tokens"] = None
    return deltas


def _previous_session_delivery(conn: sqlite3.Connection, delivery_id: str) -> str | None:
    row = conn.execute(
        "SELECT p.delivery_id FROM bridge_deliveries d JOIN bridge_deliveries p "
        "ON p.session_handle=d.session_handle AND p.stream_id=d.stream_id AND p.peer_id=d.peer_id AND p.seq<d.seq "
        "WHERE d.delivery_id=? ORDER BY p.seq DESC LIMIT 1",
        (delivery_id,)).fetchone()
    return row[0] if row else None


def _delivery_telemetry(conn: sqlite3.Connection, delivery_id: str | None) -> dict[str, Any]:
    if delivery_id is None:
        return {}
    row = conn.execute(
        "SELECT detail FROM bridge_evidence WHERE delivery_id=? AND kind='session_telemetry' ORDER BY seq DESC LIMIT 1",
        (delivery_id,)).fetchone()
    return json_object(row[0] if row else None)


def ask_delivery_tokens(
    conn: sqlite3.Connection, delivery_id: str | None, telemetry: Mapping[str, Any],
) -> dict[str, int | None]:
    previous = None
    if delivery_id is not None and telemetry.get("usage", {}).get("scope") == "session_cumulative":
        previous = _delivery_telemetry(conn, _previous_session_delivery(conn, delivery_id))
    return ask_tokens(telemetry, previous)


def stream_budget_exceeded(prompt_tokens: int | None, turns: int | None, limits: tuple[int, int]) -> bool:
    token_limit, turn_limit = limits
    return prompt_tokens >= token_limit if prompt_tokens is not None else turns is not None and turns >= turn_limit


def resumed_ask_budget(conn: sqlite3.Connection, delivery_id: str, limits: tuple[int, int]) -> bool:
    row = conn.execute(
        "SELECT d.session_snapshot_json,s.reuse_count "
        "FROM bridge_deliveries d LEFT JOIN bridge_session_monitor_stats s ON s.session_handle=d.session_handle "
        "WHERE d.delivery_id=?", (delivery_id,)).fetchone()
    if row is None or json_object(row["session_snapshot_json"]).get("mode") != "resumed":
        return False
    previous = _previous_session_delivery(conn, delivery_id)
    prompt_tokens = ask_delivery_tokens(conn, previous, _delivery_telemetry(conn, previous))["prompt_tokens"]
    return stream_budget_exceeded(prompt_tokens, row["reuse_count"], limits)


@dataclass(frozen=True)
class QuotaReading:
    observation_id: str
    pool: str
    subject: str
    source: str
    observed: float
    captured: float
    remaining: float
    window_start: float
    reset: float


def comparable_pool_delta(before: QuotaReading, after: QuotaReading,
                          competitors: list[tuple[float | None, float | None]]) -> float | None:
    if ((before.pool, before.subject, before.source, before.window_start, before.reset)
            != (after.pool, after.subject, after.source, after.window_start, after.reset)
            or not 0 <= before.window_start <= before.observed < after.observed < before.reset
            or before.captured > after.captured
            or not 0 <= after.remaining <= before.remaining <= 1
            or any((start is None or start <= after.observed) and (end is None or end >= before.observed)
                   for start, end in competitors)):
        return None
    return (before.remaining - after.remaining) * 100


def _epoch(value: str | None) -> float | None:
    try:
        stamp = datetime.fromisoformat(value) if value else None
        return stamp.timestamp() if stamp is not None and stamp.tzinfo is not None else None
    except ValueError:
        return None


def _competing_calls(conn: sqlite3.Connection, delivery_id: str, provider: str, started: float,
                     stopped: float) -> list[tuple[float | None, float | None, list[str] | None]]:
    competitors: list[tuple[float | None, float | None, list[str] | None]] = []
    for row in conn.execute(
        "WITH candidates AS ("
        f"SELECT delivery_id FROM bridge_deliveries WHERE {_CONFIRMED_END}>=? UNION ALL "
        f"SELECT delivery_id FROM bridge_deliveries WHERE {_CONFIRMED_END} IS NULL) "
        "SELECT e.detail,d.terminal_at,d.result_digest,t.detail AS telemetry "
        "FROM candidates c CROSS JOIN bridge_deliveries d ON d.delivery_id=c.delivery_id "
        "CROSS JOIN bridge_invocations i ON i.delivery_id=d.delivery_id "
        "LEFT JOIN records r ON r.record_id=d.record_id "
        "LEFT JOIN bridge_session_history h ON h.session_handle=d.session_handle "
        "LEFT JOIN bridge_evidence t ON t.delivery_id=d.delivery_id AND t.kind='session_telemetry' "
        "LEFT JOIN bridge_evidence e ON e.delivery_id=d.delivery_id AND e.kind='about_to_invoke' "
        "AND json_extract(e.detail,'$.invocation_no')=i.invocation_no "
        "LEFT JOIN bridge_invocation_resolutions x ON x.delivery_id=i.delivery_id AND x.invocation_no=i.invocation_no "
        "WHERE d.delivery_id<>? AND COALESCE(h.runtime_kind,json_extract(r.metadata_json,'$.runtime_kind'),d.peer_id)=? "
        "AND (x.reason IS NULL OR x.reason='promoted_may_have_started')",
        (iso_timestamp(started), delivery_id, provider)):
        marker = json_object(row["detail"])
        start = marker.get("quota_invoked_at")
        end = _epoch(row["terminal_at"])
        if end is None and row["result_digest"] is not None:
            end = json_object(row["telemetry"]).get("observed_at")
        if (start is None or start <= stopped) and (end is None or end >= started):
            competitors.append((start, end, marker.get("quota_pools")))
    return competitors


def record_ask_pool_delta(conn: sqlite3.Connection, delivery: sqlite3.Row, stopped: float) -> None:
    """Persist a delta only for committed readings inside a confirmed, exclusive call."""
    row = conn.execute(
        "SELECT h.runtime_kind,c.detail,m.detail "
        "FROM bridge_session_history h JOIN records r ON r.record_id=? "
        "JOIN bridge_evidence c ON c.delivery_id=? AND c.kind='session_call' "
        "JOIN bridge_evidence m ON m.delivery_id=c.delivery_id AND m.kind='about_to_invoke' "
        f"WHERE h.session_handle=? AND {ASK_RECORD} ORDER BY m.seq DESC LIMIT 1",
        (delivery["record_id"], delivery["delivery_id"], delivery["session_handle"])).fetchone()
    if row is None:
        return
    provider, call, marker = row[0], json_object(row[1]), json_object(row[2])
    pools: list[str] | None = marker.get("quota_pools")
    started: float | None = call.get("started_at")
    if (not pools or started is None or started >= stopped
            or not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='observations'").fetchone()
            or not any(column[1] == "effective_at_us" for column in conn.execute("PRAGMA table_info(observations)"))):
        return
    alias = CLI_PEER_ALIASES[provider]
    pool_order = {pool: rank for rank, pool in enumerate(pools)}
    readings: dict[tuple[str, str], tuple[QuotaReading, QuotaReading]] = {}
    selected: tuple[QuotaReading, QuotaReading, float] | None = None
    selected_rank = (len(pools), 0.0)
    competitors: list[tuple[float | None, float | None, list[str] | None]] | None = None
    for obs in conn.execute(
        "SELECT observation_id,resource_pool_ref,subject_ref,source,state,payload_json,observed_at,captured_at "
        "FROM observations WHERE subject_ref IN (?,?) AND kind='quota' AND effective_at_us BETWEEN ? AND ? "
        "ORDER BY effective_at_us,capture_seq", (provider, alias, int(started * 1e6), int(stopped * 1e6))):
        obs_id, pool, subject, source, state, payload, observed_at, captured_at = tuple(obs)
        if pool not in pool_order:
            continue
        prior = readings.pop((pool, subject), None)
        observed, captured = _epoch(observed_at), _epoch(captured_at)
        data = json_object(payload)
        remaining, window_start, reset = (data.get(k) for k in ("remaining_fraction", "window_started_at", "resets_at"))
        if (state != "MEASURED" or observed is None or captured is None or not started <= observed <= captured <= stopped
                or not valid_observed_at(remaining) or not valid_observed_at(window_start) or not valid_observed_at(reset)):
            continue
        after = QuotaReading(obs_id, pool, subject, source, observed, captured, remaining, window_start, reset)
        readings[pool, subject] = (after, after)
        if prior is None:
            continue
        if competitors is None:
            competitors = _competing_calls(conn, delivery["delivery_id"], provider, started, stopped)
        anchor, before = prior
        # Default Claude models can change outside PeerHub, so any Claude call
        # may be a competitor on the provider's Fable-only pool.
        overlap = [(start, end) for start, end, scope in competitors
                   if scope is None or pool in scope or pool == "quota:cc:F-7D"]
        if comparable_pool_delta(before, after, overlap) is None:
            continue
        readings[pool, subject] = (anchor, after)
        rank = (pool_order[pool], anchor.observed - after.observed)
        if rank < selected_rank:
            selected = (anchor, after, (anchor.remaining - after.remaining) * 100)
            selected_rank = rank
    if selected is not None:
        before, after, delta = selected
        conn.execute("INSERT INTO bridge_ask_pool_deltas VALUES (?,?,?,?,?)",
                     (delivery["delivery_id"], before.pool, before.observation_id, after.observation_id, delta))
