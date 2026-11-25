"""Read-only queries for delivery invocations and their process evidence."""
from __future__ import annotations

import sqlite3


# These reads are correlated to the selected delivery. Each invocation commits
# its marker and caller snapshot together. Older stores have separate caller
# evidence and unnumbered starts, which are bounded by the marker's sequence.
LATEST_INVOCATION = (
    "LEFT JOIN bridge_invocations i ON i.delivery_id=d.delivery_id AND i.invocation_no=("
    "SELECT MAX(n.invocation_no) FROM bridge_invocations n WHERE n.delivery_id=d.delivery_id) "
)


def invocation_marker_join(delivery: str) -> str:
    return (
        "LEFT JOIN bridge_evidence m ON m.seq=(SELECT e.seq FROM bridge_evidence e "
        f"WHERE e.delivery_id={delivery} AND e.kind='about_to_invoke' ORDER BY e.seq DESC LIMIT 1) "
    )


def invocation_process_fields(delivery: str) -> str:
    return (
        "CASE WHEN json_type(CASE WHEN json_valid(m.detail) THEN m.detail END,'$.caller') IS NOT NULL "
        "THEN json_extract(m.detail,'$.caller') ELSE "
        f"(SELECT e.detail FROM bridge_evidence e WHERE e.delivery_id={delivery} AND e.kind='caller' "
        "AND (m.seq IS NULL OR e.seq<m.seq) ORDER BY e.seq DESC LIMIT 1) END AS caller_detail,"
        f"(SELECT e.detail FROM bridge_evidence e WHERE e.delivery_id={delivery} AND e.kind IN ('started','launch_reserved') "
        "AND (m.seq IS NULL OR e.seq>m.seq) AND ("
        "json_type(CASE WHEN json_valid(e.detail) THEN e.detail END,'$.invocation_no') IS NULL OR "
        "json_extract(CASE WHEN json_valid(e.detail) THEN e.detail END,'$.invocation_no')="
        "json_extract(CASE WHEN json_valid(m.detail) THEN m.detail END,'$.invocation_no')) "
        "ORDER BY e.seq DESC LIMIT 1) AS provider_detail "
    )


def _running_deliveries_sql(*, invoked: bool, indexed: bool) -> str:
    # Drive from live claims and skip settled history inside a live scope.
    # Older stores can read without the invocation table or partial index.
    generation = "COALESCE(i.claim_generation,d.claim_generation)" if invoked else "d.claim_generation"
    index_hint = " INDEXED BY idx_bd_running_scope" if indexed else ""
    return (
        "SELECT d.delivery_id,d.peer_id,d.stream_id,d.certainty,c.expires_at,r.idempotency_key,r.metadata_json,"
        "(SELECT e.detail FROM bridge_evidence e WHERE e.delivery_id=d.delivery_id AND e.kind='session_call' "
        "AND e.certainty='STARTED' ORDER BY e.seq DESC LIMIT 1) AS start_detail,"
        + invocation_process_fields("d.delivery_id")
        + f"FROM bridge_claims c CROSS JOIN bridge_deliveries d{index_hint} ON d.stream_id=c.stream_id AND d.peer_id=c.peer_id "
        + (LATEST_INVOCATION if invoked else "")
        + invocation_marker_join("d.delivery_id")
        + "LEFT JOIN records r ON r.record_id=d.record_id "
        f"WHERE {generation}=c.generation AND c.expires_at>? AND (? IS NULL OR c.workspace_generation=?) AND d.acked=0 "
        "AND (? IS NULL OR c.peer_id=?) AND d.certainty IN ('NOT_STARTED','STARTED') "
        "ORDER BY d.seq,d.delivery_id LIMIT ?"
    )


def running_delivery_rows(conn: sqlite3.Connection, *, read_at: float, limit: int | None,
                          workspace_generation: str | None, peer: str | None) -> list[sqlite3.Row]:
    """Read lease-active delivery rows in the caller's snapshot."""
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"bridge_claims", "bridge_deliveries", "bridge_evidence"} <= tables:
        return []
    indexed = conn.execute("SELECT 1 FROM sqlite_master WHERE name='idx_bd_running_scope' AND type='index'").fetchone() is not None
    return conn.execute(
        _running_deliveries_sql(invoked="bridge_invocations" in tables, indexed=indexed),
        (read_at, workspace_generation, workspace_generation, peer, peer, -1 if limit is None else limit + 1),
    ).fetchall()
