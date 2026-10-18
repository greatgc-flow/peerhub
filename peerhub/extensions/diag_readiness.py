"""Cached readiness projection. Terminal certainty does not prove task success."""
from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from peerhub.extensions.adapters.profiles import inspect_model_pools, list_profiles
from peerhub.extensions.adapters.base import classify_refusal_text
from peerhub.extensions.diag import ReadonlyDiag


def readiness_report(db_path: str | Path, *, read_at: float | None = None,
                     reader: ReadonlyDiag | None = None) -> dict[str, Any]:
    db = Path(db_path).resolve()
    now = time.time() if read_at is None else read_at
    workspace = db.parent.parent if db.parent.name == ".peerhub" else Path.cwd()
    try:
        profiles = configured_profiles(workspace) if db.is_file() else []
    except (ValueError, OSError) as exc:
        return dict(schema_version="1.0", read_at=now, status="FAILED", basis="cached",
                    pools={}, profiles=[], error=f"{type(exc).__name__}: {exc}")
    snapshot = (reader or ReadonlyDiag(db)).readiness([p["id"] for p in profiles], read_at=now)
    if snapshot["status"] != "OK":
        return {key: snapshot[key] for key in ("schema_version", "read_at", "status", "basis", "error")} | {"pools": {}, "profiles": []}
    return compose(profiles, snapshot["observations"], snapshot["deliveries"], snapshot["read_at"], complete=snapshot["complete"])


def configured_profiles(workspace: Path) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for item in list_profiles(workspace, include_pool_source=True):
        fields = item["fields"]
        model, effort = fields["model"]["value"], fields["effort"]["value"]
        exact = inspect_model_pools(model, workspace) if model else None
        pools: list[str] = exact["pools"] if exact is not None else fields["pools"]["value"] or []
        verified = exact["verified"] if exact is not None else (
            fields["model"]["source"] == fields["pools"]["source"]
            and fields["pools_source"]["source"] == fields["pools"]["source"]
            and fields["pools_source"]["value"] not in (None, "", "unverified"))
        provider = item["profile"].split(".", 1)[0]
        if exact is None and fields["model"]["source"] != fields["pools"]["source"]:
            pools = []  # Membership declared for another model cannot follow an override.
        refs = sorted({p if p.startswith("quota:") else f"quota:{provider}:{p}" for p in pools})
        binding = f"{item['profile']}/{model or 'default'}/{effort or 'default'}"
        profiles.append({"id": item["profile"], "configured_model": model, "configured_effort": effort,
                         "provenance": fields, "pools": refs, "mapping": "verified" if verified and refs else "unverified",
                         "binding": binding, "policy": "requires_writable_consent" if provider == "ag" else None})
    return profiles


def _object(raw: object) -> dict[str, Any]:
    value = json.loads(str(raw))
    if not isinstance(value, dict):
        raise ValueError("expected stored JSON object")
    return cast(dict[str, Any], value)


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _matches_binding(recorded: str, configured: str) -> bool:
    # Native bindings append an opaque cwd/write-mode hash for session reuse.
    # Readiness compares the recorded profile/model/effort, independently of
    # where a store is inspected. Cached delivery never grants write consent.
    binding, separator, scope = recorded.rpartition("#")
    if separator and re.fullmatch(r"[0-9a-f]{12}", scope):
        recorded = binding
    return recorded == configured


def compose(profiles: list[dict[str, Any]], observations: list[dict[str, Any]],
            deliveries: list[dict[str, Any]], now: float, *, complete: bool) -> dict[str, Any]:
    pools: dict[str, Any] = {}
    for ref in sorted({ref for profile in profiles for ref in profile["pools"]}):
        items = [o for o in observations if o["resource_pool_ref"] == ref and o["kind"] in ("quota", "rate_limit")]
        item = max(items, key=lambda o: (o["observed_at"] or o["captured_at"], o["capture_seq"]), default=None)
        view: dict[str, Any] = dict(state="UNKNOWN", remaining=None, reset_in=None, age=None, evidence=None)
        if item:
            view.update(state=item["state"], age=item["age_seconds"], evidence=f"observation:{item['observation_id']}")
            if item["state"] == "MEASURED":
                remaining, reset = item["payload"].get("remaining_fraction"), item["payload"].get("resets_at")
                if _number(remaining) and 0 <= remaining <= 1:
                    view["remaining"] = remaining
                if _number(reset):
                    view["reset_in"] = max(0, reset - now)
        pools[ref] = view
    latest: dict[str, dict[str, Any]] = {}
    corrupt = not complete
    for row in deliveries:
        try:
            meta = _object(row["metadata_json"])
            binding = meta.get("binding")
            if not isinstance(binding, str):
                raise ValueError("missing recorded binding")
            profile_id = binding.split("/", 1)[0]
            # Rows arrive newest first; never replace a newer failure with old success.
            latest.setdefault(profile_id, row)
        except (ValueError, TypeError):
            corrupt = True
    for profile in profiles:
        row = latest.get(profile["id"])
        last: dict[str, Any] = dict(outcome="unverified", age=None, error_class="UNKNOWN")
        refusal = False
        if row:
            last["evidence"] = f"delivery:{row['delivery_id']}"
            try:
                matching = _matches_binding(_object(row["metadata_json"])["binding"], profile["binding"])
                result = _object(row["result_json"]) if row["result_json"] is not None else None
                detail = _object(row["detail"]) if row["detail"] is not None else {}
                cause = detail.get("error_class") or detail.get("refusal_reason") or detail.get("reason")
                error_text = detail.get("error")
                if result:
                    error = result.get("error")
                    cause = cause or result.get("error_class") or result.get("reason") or (cast(dict[str, Any], error).get("code") if isinstance(error, dict) else None)
                    if isinstance(error, dict):
                        error_text = cast(dict[str, Any], error).get("message") or error_text
                if not cause and isinstance(error_text, str):
                    recognized = classify_refusal_text(error_text)
                    cause = recognized.get("reason") if recognized else None
                causes = {"quota_exhausted": "rate_limit", "usage_limit": "rate_limit", "rate_limit": "rate_limit",
                          "auth_failure": "auth_failure", "invalid_model": "invalid_model"}
                last["error_class"] = causes.get(cause, "UNKNOWN") if isinstance(cause, str) else "UNKNOWN"
                refusal = row["kind"] == "refusal_detected" or bool(result and result.get("task_outcome") == "NOT_EXECUTED")
                if not matching:
                    last["outcome"] = "binding_changed"
                elif refusal:
                    last["outcome"] = "refused"
                elif row["certainty"] == "TERMINAL" and result is not None and not result.get("error"):
                    last["outcome"] = "terminal"
                elif row["certainty"] in ("STARTED", "MAY_HAVE_STARTED"):
                    last["outcome"] = "uncertain"
                else:
                    last["outcome"] = "failed"
                at = row["terminal_at"] or row["appended_at"]
                recorded_at = datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp()
                if recorded_at > now:
                    raise ValueError("delivery evidence is in the future")
                last["age"] = now - recorded_at
            except (ValueError, TypeError, KeyError, OverflowError):
                corrupt = True
                last.update(outcome="unverified", error_class="UNKNOWN")
        required = [pools[ref] for ref in profile["pools"]]
        known = profile["mapping"] == "verified"
        exhausted = known and any(p["state"] == "MEASURED" and p["remaining"] == 0 for p in required)
        satisfied = known and all(p["state"] == "MEASURED" and p["remaining"] is not None and p["remaining"] > 0 for p in required)
        state = ("UNKNOWN" if corrupt else "EXHAUSTED" if exhausted else "UNKNOWN" if not satisfied else
                 "READY" if last["outcome"] == "terminal" and not profile["policy"] and last["age"] is not None else "UNVERIFIED")
        action = ("ask_uncertain" if last["outcome"] == "uncertain" else
                  "ask_refusal" if refusal else "ask_delivery_failed" if profile["policy"] else
                  "wait_reset" if exhausted and any(p["remaining"] == 0 and p["reset_in"] is not None for p in required)
                  else "inspect_facts" if state != "READY" else None)
        profile.update(state=state, last=last, next=None, action=action, stream_id=row["stream_id"] if row else None)
        del profile["binding"]
    if corrupt:
        for profile in profiles:
            profile["state"] = "UNKNOWN"
            if profile["action"] is None:
                profile["action"] = "inspect_facts"
    return {"schema_version": "1.0", "read_at": now, "status": "OK", "basis": "cached",
            "pools": pools, "profiles": profiles, "error": None}


def format_readiness(report: dict[str, Any]) -> str:
    lines = [f"Cached readiness: {report['status']} (READY=cached criteria satisfied; delivery certainty only)", "PROFILE POOLS STATE LAST NEXT"]
    aliases = {ref: str(n) for n, ref in enumerate(report["pools"], 1)}
    for p in report["profiles"][:9]:
        last = p["last"]
        age = "?" if last["age"] is None else f"{last['age']:.0f}s"
        action = {"ask_uncertain": "health", "ask_refusal": "refusal", "ask_delivery_failed": "consent",
                  "wait_reset": "wait", "inspect_facts": "facts"}.get(p["next"]["code"], "facts") if p["next"] else "-"
        label = last["error_class"] if last["error_class"] != "UNKNOWN" else last["outcome"]
        outcome = "-" if last["age"] is None and "evidence" not in last else f"{label}@{age}"
        lines.append(f"{p['id']} {','.join(aliases[r] for r in p['pools']) or '?'} {p['state']} {outcome} {action}")
    if len(report["profiles"]) > 9:
        lines.append(f"+{len(report['profiles']) - 9} profiles, see --json")
    for ref, pool in report["pools"].items():
        rem = "?" if pool["remaining"] is None else f"{pool['remaining']:.0%}"
        reset = "?" if pool["reset_in"] is None else f"{pool['reset_in']:.0f}s"
        age = "?" if pool["age"] is None else f"{pool['age']:.0f}s"
        lines.append(f"{aliases[ref]}={ref} REM={rem} reset={reset} age={age} {pool['state']}")
    if report["error"]:
        lines.append(report["error"])
    return "\n".join(lines)
