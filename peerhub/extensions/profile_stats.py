"""Read stored ask telemetry and benchmark measurements for profile inspection."""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from statistics import median
import subprocess
from typing import Any, Iterable, Mapping, cast

from peerhub.core.connection import create_connection
from peerhub.core.models import utc_now_iso
from peerhub.core.schema_version import SchemaVersionError
from peerhub.core.store import storage_errors
from peerhub.extensions.ask_budgets import ASK_RECORD, ask_delivery_tokens, install_ask_metrics
from peerhub.extensions.quota_probes import real_command, resolve_sys_dir
from peerhub.extensions.schema_guard import refuse_future_schema
from peerhub.extensions.session_receipts import json_object


OUTCOMES = ("accepted", "rejected", "retried")
TOKEN_METRICS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "prompt_tokens")
METRICS = ("wall_seconds", *TOKEN_METRICS, "cache_read_pct", "pool_delta_pp", "model", "effort", "mode")


def mark_outcome(db: str, ask_id: str, outcome: str) -> None:
    path = Path(db)
    if not path.is_file():
        raise ValueError(f"ask {ask_id!r} was not found in the selected store")
    refuse_future_schema(path)
    with storage_errors(), closing(create_connection(path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if (not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='records'").fetchone()
                or not conn.execute(f"SELECT 1 FROM records r WHERE r.record_id=? AND {ASK_RECORD}", (ask_id,)).fetchone()):
            raise ValueError(f"ask {ask_id!r} was not found in the selected store")
        install_ask_metrics(conn)
        conn.execute("INSERT INTO bridge_ask_outcomes(record_id,outcome,marked_at) VALUES (?,?,?)",
                     (ask_id, outcome, utc_now_iso()))


def _binding(raw: str) -> tuple[str | None, str | None, str | None]:
    parts = raw.split("#", 1)[0].split("/")
    profile, model, effort = (parts if len(parts) == 3 else [None, *parts] if len(parts) == 2 else [None, None, None])
    return profile, model if model != "default" else None, effort if effort != "default" else None


def _ask_rows(conn: sqlite3.Connection, ask_id: str | None) -> Iterable[sqlite3.Row]:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "records" not in tables:
        return
    columns = {r[1] for r in conn.execute("PRAGMA table_info(bridge_deliveries)")}
    joins: list[str] = []
    fields = ["r.record_id", "r.created_at", "json_extract(r.targets_json,'$[0]') AS peer",
              "json_extract(r.metadata_json,'$.binding') AS binding"]
    if columns:
        joins.append("LEFT JOIN bridge_deliveries d ON d.delivery_id=(SELECT delivery_id FROM bridge_deliveries "
                     "WHERE record_id=r.record_id AND peer_id=json_extract(r.targets_json,'$[0]') ORDER BY attempt DESC LIMIT 1)")
    fields.append("d.delivery_id AS delivery_id" if "session_handle" in columns else "NULL AS delivery_id")
    fields.append("d.session_snapshot_json AS session" if "session_snapshot_json" in columns else "NULL AS session")
    fields.append("d.terminal_at AS terminal_at" if columns else "NULL AS terminal_at")
    if columns and "bridge_evidence" in tables:
        joins.append("LEFT JOIN bridge_evidence u ON u.seq=(SELECT seq FROM bridge_evidence WHERE delivery_id=d.delivery_id "
                     "AND kind='session_telemetry' ORDER BY seq DESC LIMIT 1)")
        joins.append("LEFT JOIN bridge_evidence t ON t.seq=(SELECT seq FROM bridge_evidence WHERE delivery_id=d.delivery_id "
                     "AND kind='terminal' AND certainty='TERMINAL' ORDER BY seq LIMIT 1)")
        fields.extend(("u.detail AS telemetry", "t.detail AS terminal"))
    else:
        fields.extend(("NULL AS telemetry", "NULL AS terminal"))
    if "bridge_ask_pool_deltas" in tables and columns:
        joins.append("LEFT JOIN bridge_ask_pool_deltas p ON p.delivery_id=d.delivery_id")
        fields.extend(("p.delta_pp AS pool_delta_pp", "p.pool_id AS pool_id"))
    else:
        fields.extend(("NULL AS pool_delta_pp", "NULL AS pool_id"))
    outcome = ("(SELECT outcome FROM bridge_ask_outcomes WHERE record_id=r.record_id ORDER BY seq DESC LIMIT 1)"
               if "bridge_ask_outcomes" in tables else "NULL")
    fields.append(f"COALESCE({outcome},'unknown') AS outcome")
    where = f"{ASK_RECORD} AND r.record_id=?" if ask_id is not None else ASK_RECORD
    yield from conn.execute(f"SELECT {', '.join(fields)} FROM records r {' '.join(joins)} WHERE {where}",
                            (ask_id,) if ask_id is not None else ())


def _timestamp(value: object) -> datetime | None:
    try:
        if isinstance(value, str):
            stamp = datetime.fromisoformat(value)
            return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
        if type(value) in (float, int):
            return datetime.fromtimestamp(cast(float, value), tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        pass
    return None


def cache_share(read: int | None, prompt: int | None) -> float | None:
    return 100 * read / prompt if read is not None and prompt and 0 <= read <= prompt else None


def ask_measurements(db: str, ask_id: str | None = None) -> Iterable[dict[str, Any]]:
    path = Path(db)
    if not path.is_file() or not path.stat().st_size:
        return
    refuse_future_schema(path)
    with storage_errors(), closing(create_connection(path, readonly=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")  # schema discovery and evidence reads share one snapshot
        for row in _ask_rows(conn, ask_id):
            profile, model, effort = _binding(row["binding"])
            session = json_object(row["session"])
            telemetry = json_object(row["telemetry"])
            tokens = ask_delivery_tokens(conn, row["delivery_id"], telemetry)
            elapsed = json_object(row["terminal"]).get("peerhub_timing", {}).get("elapsed_ns")
            stamp = (_timestamp(row["terminal_at"]) or _timestamp(telemetry.get("observed_at"))
                     or _timestamp(row["created_at"]))
            yield dict(ask_id=row["record_id"], peer=row["peer"], profile=profile, model=model, effort=effort,
                       mode=session.get("mode"), wall_seconds=elapsed / 1e9 if type(elapsed) is int and elapsed >= 0 else None,
                       **tokens, cache_read_pct=cache_share(tokens["cache_read_tokens"], tokens["prompt_tokens"]),
                       pool_delta_pp=row["pool_delta_pp"], pool_id=row["pool_id"], outcome=row["outcome"], measured_at=stamp)


def aggregate_profile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    observed = {metric: [r[metric] for r in rows if r.get(metric) is not None] for metric in METRICS}
    outcome_counts = Counter(row["outcome"] for row in rows)
    accepted = outcome_counts["accepted"]
    cache_rows = [r for r in rows if r.get("cache_read_pct") is not None]
    deltas = observed["pool_delta_pp"]
    tokens_measured = sum(row.get("input_tokens") is not None and row.get("output_tokens") is not None for row in rows)
    return {
        "n": len(rows),
        "coverage_pct": {metric: 100 * len(values) / len(rows) if rows else None for metric, values in observed.items()},
        "token_coverage_pct": 100 * tokens_measured / len(rows) if rows else None,
        "p50_wall_seconds": median(observed["wall_seconds"]) if observed["wall_seconds"] else None,
        "cache_read_pct": cache_share(sum(r["cache_read_tokens"] for r in cache_rows), sum(r["prompt_tokens"] for r in cache_rows)),
        "tokens_per_ask": {m: sum(observed[m]) / len(observed[m]) if observed[m] else None for m in TOKEN_METRICS},
        "pool_pct_per_accepted_answer": sum(deltas) / accepted if accepted and deltas else None,
        "outcomes": {outcome: outcome_counts[outcome] for outcome in ("unknown", *OUTCOMES)},
    }


def _benchmarks(workspace: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    suite = workspace / "docs" / "benchmarks" / "suite-v1"
    for path in sorted(suite.glob("results*.jsonl")):
        with path.open(encoding="utf-8") as lines:
            for line in lines:
                try:
                    row = json_object(line)
                except ValueError:
                    continue  # a runner may still be appending the final line
                profile, stamp = row.get("profile"), _timestamp(row.get("finished_at"))
                if isinstance(profile, str) and stamp is not None and (profile not in latest or stamp >= latest[profile]["measured_at"]):
                    latest[profile] = dict(row, measured_at=stamp)
    return latest


def _configured_binding(profile: Mapping[str, Any]) -> tuple[str | None, str | None]:
    fields = profile["fields"]
    return fields["model"]["value"], fields["effort"]["value"]


def _is_measured(row: Mapping[str, Any]) -> bool:
    return row["measured_at"] is not None and any(row[key] is not None for key in ("wall_seconds", "pool_delta_pp", *TOKEN_METRICS))


def _stamp(stamp: datetime) -> str:
    return stamp.isoformat().replace("+00:00", "Z")


def read_profile_stats(db: str, workspace: Path, profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cohorts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ask_measurements(db):
        if row["profile"] is not None:
            cohorts[row["profile"]].append(row)
    benchmarks = _benchmarks(workspace)
    result: list[dict[str, Any]] = []
    for profile in profiles:
        name = profile["profile"]
        rows = cohorts[name]
        benchmark = benchmarks.get(name)
        measurements = [r for r in rows if _is_measured(r)]
        if benchmark is not None and "model" in benchmark and "effort" in benchmark:
            measurements.append(benchmark)
        latest = max(measurements, key=lambda r: (r["measured_at"], r.get("ask_id", "")), default=None)
        model, effort = _configured_binding(profile)
        stale = latest is not None and (latest.get("model"), latest.get("effort")) != (model, effort)
        measurement = profile["fields"]["measurement"]["value"]
        if measurement == "unmeasured" and any((r.get("model"), r.get("effort")) == (model, effort) for r in measurements):
            measurement = "measured"
        result.append(dict(profile=name, model=model, effort=effort, **aggregate_profile(rows),
                           last_benchmark_run=_stamp(benchmark["measured_at"]) if benchmark else None,
                           last_measured_at=_stamp(latest["measured_at"]) if latest else None,
                           stale=stale, measurement=measurement))
    return result


def installed_models(provider: str, workspace: Path) -> set[str] | None:
    try:
        command = real_command(provider, resolve_sys_dir())
        if command is None:
            return None
        tail = ["debug", "models"] if provider == "cx" else ["models"]
        result = subprocess.run([*command, *tail], cwd=workspace, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=15, encoding="utf-8", errors="replace", check=True)
        if provider == "cx":
            payload: object = json.JSONDecoder().raw_decode(result.stdout.lstrip())[0]
            entries: object = cast(dict[str, Any], payload).get("models") if isinstance(payload, dict) else None
            if not isinstance(entries, list):
                return None
            models: set[str] = set()
            for entry in cast(list[object], entries):
                slug: object = cast(dict[str, Any], entry).get("slug") if isinstance(entry, dict) else None
                if isinstance(slug, str) and slug:
                    models.add(slug)
        else:
            models = {slug.strip() for line in result.stdout.splitlines() for slug, tab, label in [line.partition("\t")]
                      if tab and slug.strip() and label.strip()}
        return models or None  # an empty or unrecognized response is inconclusive
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError):
        return None


def check_profiles(profiles: list[dict[str, Any]], stats: list[dict[str, Any]], workspace: Path) -> dict[str, Any]:
    catalogs = {provider: installed_models(provider, workspace) for provider in ("cx", "ag")}
    missing: dict[str, str] = {}
    for profile in profiles:
        name = profile["profile"]
        model, _ = _configured_binding(profile)
        models = catalogs.get(name.split(".", 1)[0])
        if model is not None and models is not None and model not in models:
            missing[name] = model
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    overdue: list[str] = []
    for row in stats:
        measured = _timestamp(row["last_measured_at"])
        if row["stale"] or measured is None or measured < cutoff:
            overdue.append(row["profile"])
    return {"schema_version": "1.0", "missing_models": missing, "measurement_overdue": overdue}


def read_receipt_measurements(db: str, ask_id: str) -> dict[str, Any]:
    """Read optional telemetry without changing a completed ask's exit status."""
    empty: dict[str, Any] = {}
    try:
        return next(iter(ask_measurements(db, ask_id)), empty)
    except (OSError, sqlite3.Error, SchemaVersionError):
        return empty
