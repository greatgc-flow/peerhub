"""Project retained sessions, telemetry, and runtime binding provenance."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, cast

from peerhub.extensions.monitor_queries import session_rows
from peerhub.extensions.monitor_values import SAFE_MODEL, finite_number, public_binding_value


def _safe_object(raw: object) -> dict[str, Any]:
    if not isinstance(raw, str):
        return {}
    try:
        parsed: object = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else {}


def current_profile(profile: object, workspace: Path) -> dict[str, Any]:
    if not isinstance(profile, str) or public_binding_value(profile) is None:
        return {"basis": "current_config", "profile": None, "fields": None, "reason": "profile_unavailable"}
    from peerhub.extensions.adapters.profiles import inspect_profile
    try:
        current = inspect_profile(profile, workspace)
    except (OSError, ValueError):
        return {"basis": "current_config", "profile": profile, "fields": None, "reason": "profile_config_unavailable"}
    return {"basis": "current_config", **current}


def monitor_workspace(db_path: str, workspace: str | Path | None) -> Path:
    if workspace is not None:
        return Path(workspace)
    database = Path(db_path).resolve()
    if database.parent.name == ".peerhub":
        return database.parent.parent
    if (database.parent / "workspace.generation").is_file():
        return database.parent
    return Path.cwd()


def context_estimate_age(observed_at: float | None, now: float | None) -> float | None:
    """Seconds since the context sample, clamped at zero for clock skew."""
    return None if observed_at is None or now is None else max(0.0, now - observed_at)


def public_telemetry(raw: object, *, handle: str, generation: int, provider: str) -> dict[str, Any]:
    """Validate and redact one session receipt for monitor and session inspection."""
    detail = _safe_object(raw)
    if (detail.get("schema_version") != 3 or detail.get("handle") != handle or
            detail.get("generation") != generation or detail.get("provider_kind") != provider):
        return {"configured_model": None, "resolved_model": None, "observed_at": None,
                "adapter_version": None, "provider_cli_version": None, "usage": None,
                "context": {"occupancy_tokens": None, "window_tokens": None, "percent": None,
                            "source": None, "observed_at": None, "window_reported_at": None,
                            "state": "UNKNOWN", "reason": "invalid_or_missing_receipt"}}
    def model(value: object) -> str | None:
        return value if isinstance(value, str) and SAFE_MODEL.fullmatch(value) else None
    from peerhub.extensions.session_receipts import CONTEXT_SOURCE, context_estimate
    from peerhub.extensions.usage_normalization import validate_provider_usage
    usage_raw: object = detail.get("usage")
    public_usage: dict[str, Any] | None = None
    if isinstance(usage_raw, dict):
        usage = cast(dict[str, Any], usage_raw)
        candidate = {key: usage.get(key) for key in ("schema_version", "scope", "source", "reported", "normalized", "coverage")}
        checked = validate_provider_usage(candidate, provider)
        if checked == usage:
            public_usage = {key: usage.get(key) for key in ("schema_version", "scope", "attribution", "reported", "normalized", "coverage")}
    occupancy, window, pct = detail.get("last_input_tokens"), detail.get("context_window"), detail.get("context_pct")
    sample_at, window_at = detail.get("observed_at"), detail.get("window_reported_at")
    # One validated source (#376 P2): the cc adapter's reading of the ask's own stream, an ESTIMATE of the occupancy at the
    # end of that ask (`observed_at`). Every other source, provider or value that fails a check stays unknown.
    estimate = context_estimate(occupancy, window) if provider == "cc" and detail.get("source") == CONTEXT_SOURCE else None
    valid_context = (estimate is not None and finite_number(pct) == estimate[2]
                     and finite_number(sample_at) is not None and finite_number(window_at) is not None)
    has_context = any(detail.get(key) is not None for key in
                      ("last_input_tokens", "context_window", "context_pct", "context_sample"))
    tokens, size, percent = estimate if estimate is not None and valid_context else (None, None, None)
    context = {"occupancy_tokens": tokens, "window_tokens": size, "percent": percent,
               "source": model(detail.get("source")),
               "observed_at": sample_at if valid_context else None,
               "window_reported_at": window_at if valid_context else None,
               "state": "ESTIMATED" if valid_context else "UNKNOWN",
               "reason": None if valid_context else "unvalidated_context_source" if has_context else "no_context_measurement"}
    return {"configured_model": model(detail.get("configured_model")), "resolved_model": model(detail.get("resolved_model")),
            "adapter_version": public_binding_value(detail.get("adapter_version")),
            "provider_cli_version": public_binding_value(detail.get("provider_cli_version")),
            "observed_at": sample_at if isinstance(sample_at, (int, float)) and not isinstance(sample_at, bool) else None,
            "usage": public_usage, "context": context}


def read_session_groups(conn: sqlite3.Connection, groups: list[str], output: dict[str, Any],
                        errors: dict[str, str], db_path: str, workspace: str | Path | None
                        ) -> tuple[list[dict[str, Any]], bool]:
    """Read one bounded sample for the session, usage, context and runtime groups."""
    sessions: list[dict[str, Any]] = []
    truncated = False
    sessions_total = 0
    try:
        needs_sessions = bool(set(groups) & {"overview", "sessions", "usage", "context", "runtime", "peers"})
        rows = session_rows(conn) if needs_sessions else []
        truncated = len(rows) > 100
        if truncated:  # the count that makes the sample marker "sample 100/M"
            sessions_total = int(rows[0]["total_sessions"])
        for row in rows[:100]:
            binding = _safe_object(row["binding_json"])
            telemetry = public_telemetry(row["detail"], handle=str(row["session_handle"]),
                                         generation=int(row["session_generation"]), provider=str(row["runtime_kind"]))
            rc, fc = row["reuse_count"], row["fresh_count"]
            exec_mode = ("reused" if rc and rc > 0 else "fresh" if fc and fc > 0 else None)
            sessions.append({"handle": str(row["session_handle"]), "stream_id": str(row["stream_id"]),
                             "peer_id": str(row["peer_id"]), "generation": int(row["session_generation"]),
                             "provider": str(row["runtime_kind"]), "state": str(row["state"]),
                             "resumable": bool(row["resumable"]), "execution_mode": exec_mode,
                             "uses": None if rc is None or fc is None else int(rc) + int(fc),
                             "last_seen": float(row["last_seen"]),
                             "created_at": float(row["created_at"]), "tombstoned_at": row["tombstoned_at"],
                             "configured_binding": {key: public_binding_value(binding.get(key)) for key in ("profile", "model", "effort")},
                             "telemetry": telemetry})
    except sqlite3.Error as exc:
        errors["sessions"] = type(exc).__name__
    # One session sample feeds sessions, usage and context (#394): they carry the same size, truncation and total.
    sample = {"sample_size": len(sessions), "truncated": truncated, **({"sample_total": sessions_total} if truncated else {})}
    if "sessions" in groups:
        output["sessions"] = {"status": "ERROR" if "sessions" in errors else "OK",
                              "data": {"items": sessions, **sample}}
    if "usage" in groups:
        output["usage"] = {"status": "ERROR" if "sessions" in errors else "OK", "data": {"sessions": [{"handle": item["handle"], "peer_id": item["peer_id"],
                                                                        "generation": item["generation"], "telemetry": item["telemetry"]["usage"]}
                                                               for item in sessions], **sample}}
    if "context" in groups:
        output["context"] = {"status": "ERROR" if "sessions" in errors else "OK", "data": {"sessions": [{"handle": item["handle"], "peer_id": item["peer_id"],
                                                                          "generation": item["generation"], "context": item["telemetry"]["context"]}
                                                                 for item in sessions], **sample}}
    if "runtime" in groups:
        config_workspace = monitor_workspace(db_path, workspace)
        current_profiles: dict[str, dict[str, Any]] = {}
        runtime_sessions: list[dict[str, Any]] = []
        for item in sessions:
            binding = item["configured_binding"]
            profile = binding.get("profile")
            if isinstance(profile, str):
                if profile not in current_profiles:
                    current_profiles[profile] = current_profile(profile, config_workspace)
                profile_facts = current_profiles[profile]
            else:
                profile_facts = current_profile(profile, config_workspace)
            runtime_sessions.append({
                "handle": item["handle"], "peer_id": item["peer_id"], "provider": item["provider"],
                "configured_binding": binding, "resolved_model": item["telemetry"]["resolved_model"],
                "adapter_version": item["telemetry"]["adapter_version"],
                "provider_cli_version": item["telemetry"]["provider_cli_version"],
                "configured_binding_provenance": {
                    "basis": "session_snapshot",
                    "field_sources": {"profile": None, "model": None, "effort": None}},
                "current_profile": profile_facts,
            })
        output["runtime"] = {"status": "ERROR" if "sessions" in errors else "OK",
                              "data": {"sessions": runtime_sessions}}
    return sessions, truncated
