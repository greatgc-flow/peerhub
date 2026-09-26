"""Read-only preflight for `activate_consensus_v2` (never mutates the database).

Usage:
    python tools/consensus_preflight/consensus_activation_preflight.py <workspace.sqlite3> [--json]

Exit codes: 0 = no blockers, 1 = blockers found, 2 = could not inspect.

It answers the operational prerequisites of the consensus V2 activation
(docs/design/CONSENSUS-REPLACEMENT-ADDENDUM-2026-09-26.md A2):
  * schema is at migration 0036 or later and the activation state is a clean pre-activation;
  * no OPEN legacy (V1) consensus round exists (they are held after activation);
  * no unfinished/claimed consensus.* or ratified-invariant effect exists (drain first).
The database is opened with SQLite `mode=ro`; run it against a COPY when in doubt.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

REQUIRED_MIGRATION = 36
INVARIANT_KIND = "governance.ratified-invariant-write-request"


def inspect_database(db_path: Path) -> dict[str, Any]:
    uri = db_path.absolute().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return _inspect(connection)
    finally:
        connection.close()


def _inspect(connection: sqlite3.Connection) -> dict[str, Any]:
    blockers: list[str] = []
    info: dict[str, Any] = {}

    version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    info["schema_version"] = version
    if version is None or version < REQUIRED_MIGRATION:
        blockers.append(
            f"schema is at migration {version}, activation needs >= {REQUIRED_MIGRATION} "
            "(run `peerhub workspace init` on a backup first)"
        )
        return {"blockers": blockers, "info": info}

    # Reuse the production classifier when importable (same rule the runtime applies).
    try:
        from peerhub.persistence.consensus_activation import read_activation_state

        state = read_activation_state(connection)
    except ImportError:  # pragma: no cover - tool copied out of the repo
        state = "unknown"
    info["activation_state"] = state
    if state == "activated":
        blockers.append("consensus V2 is already activated (nothing to do)")
    elif state == "inconsistent":
        blockers.append("activation state is INCONSISTENT; repair before anything else")

    rounds: dict[str, dict[str, int]] = {"open": {}, "resolved_or_closed": {}, "v2": {}}
    open_v1: list[str] = []
    for row in connection.execute(
        "SELECT target_id, state_json FROM governed_targets WHERE target_kind = 'consensus-round'"
    ):
        state_json = json.loads(row["state_json"])
        schema = str(state_json.get("schema", ""))
        phase = str(state_json.get("phase", "?"))
        if schema.endswith(".v2"):
            bucket = rounds["v2"]
        elif state_json.get("status") == "open":
            bucket = rounds["open"]
            open_v1.append(row["target_id"])
        else:
            bucket = rounds["resolved_or_closed"]
        bucket[phase] = bucket.get(phase, 0) + 1
    info["consensus_rounds"] = rounds
    if open_v1:
        blockers.append(
            f"{len(open_v1)} OPEN legacy consensus round(s) would be held after activation: "
            + ", ".join(sorted(open_v1)[:10])
            + (" ..." if len(open_v1) > 10 else "")
            + " -- resolve, abandon or let them finish first"
        )

    effects = {"unclaimed": {}, "claimed": {}}
    blocking_effects: list[str] = []
    query = """
        SELECT d.event_id, d.claimed_by,
               json_extract(e.payload_json, '$.effect_kind') AS kind,
               json_extract(e.payload_json, '$.target_id') AS target
        FROM effect_deliveries d
        JOIN event_log e ON e.event_id = d.event_id
        WHERE NOT EXISTS (SELECT 1 FROM effect_receipts r WHERE r.outbox_event_id = d.event_id)
    """
    for row in connection.execute(query):
        kind = row["kind"] or ""
        if not (kind.startswith("consensus.") or kind == INVARIANT_KIND):
            continue
        bucket = effects["claimed" if row["claimed_by"] else "unclaimed"]
        bucket[kind] = bucket.get(kind, 0) + 1
        if kind != "consensus.noop":
            blocking_effects.append(f"{kind} on {row['target']} ({'claimed by ' + row['claimed_by'] if row['claimed_by'] else 'unclaimed'})")
    info["unfinished_effects"] = effects
    if blocking_effects:
        blockers.append(
            f"{len(blocking_effects)} unfinished consensus/invariant effect(s) must be drained first: "
            + "; ".join(blocking_effects[:5])
            + (" ..." if len(blocking_effects) > 5 else "")
        )
    return {"blockers": blockers, "info": info}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("database", type=Path, help="path to the workspace SQLite database (a copy is safest)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    if not args.database.exists():
        print(f"preflight: database not found: {args.database}", file=sys.stderr)
        return 2
    try:
        report = inspect_database(args.database)
    except (sqlite3.Error, ValueError) as exc:
        print(f"preflight: could not inspect database: {exc}", file=sys.stderr)
        return 2
    report["ok"] = not report["blockers"]
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"schema version: {report['info'].get('schema_version')}")
        print(f"activation state: {report['info'].get('activation_state')}")
        print(f"consensus rounds: {json.dumps(report['info'].get('consensus_rounds'))}")
        print(f"unfinished consensus effects: {json.dumps(report['info'].get('unfinished_effects'))}")
        if report["blockers"]:
            print("BLOCKERS:")
            for blocker in report["blockers"]:
                print(f"  - {blocker}")
        else:
            print("OK: no blockers (still take a verified backup before activating)")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
