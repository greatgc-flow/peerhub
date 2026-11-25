"""Synthesize monitor alerts and report the coverage of their sources."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any, cast

from peerhub.extensions.liveness import ORPHAN_RUNNING
from peerhub.extensions.monitor_activity import BAD_ASK_STATES
from peerhub.extensions.monitor_queries import ACTIVITY_SCOPE_CAP, ALERT_WINDOW_S
from peerhub.extensions.monitor_quota import QuotaSnapshot, capacity_source
from peerhub.core.scalars import finite_number
from peerhub.extensions.monitor_values import fit_line


TOKEN_EXPIRY_WARN_S = 48 * 3600


def _alert(code: str, kind: str, subject: str, age_s: float | None, severity: str) -> dict[str, Any]:
    return {"code": code, "kind": kind, "subject": subject, "age_s": age_s, "severity": severity}


def pool_out_alerts(pools: Iterable[str]) -> list[dict[str, Any]]:
    return [_alert("a3", "pool_out", pool, None, "high") for pool in pools]


def build_alerts(*, asks: list[dict[str, Any]] | None, pools: list[dict[str, Any]] | None,
                 accounts: list[dict[str, Any]] | None, facts_report: dict[str, Any] | None,
                 failures: list[tuple[str, str]], now: float) -> list[dict[str, Any]]:
    """Alerts in severity order: a6 failure; a2 gone; a2 overdue; a1; a5 login or expired token; a3 pool out; then the low ones
    (a5 token expiring within 48 hours, a6 facts needs_recheck). A source that was not read (None) gives no alert."""
    failed = [_alert("a6", "store", f"{group}: {reason}", None, "high") for group, reason in failures]
    gone: list[dict[str, Any]] = []
    overdue: list[dict[str, Any]] = []
    ended_badly: list[dict[str, Any]] = []
    for row in asks or []:
        if row.get("superseded"):
            continue
        subject = f"{row.get('profile') or row.get('peer') or '?'} {row.get('name') or '?'}"
        state = row.get("state")
        if state == ORPHAN_RUNNING:
            gone.append(_alert("a2", ORPHAN_RUNNING, subject, row.get("elapsed_s"), "high"))
        elif state == "run" and row.get("alive") is False:
            gone.append(_alert("a2", "gone", subject, row.get("elapsed_s"), "high"))
        elif state == "overdue":
            left = finite_number(row.get("lease_left_s"), overflow_is_missing=True)
            if left is not None and -left <= ALERT_WINDOW_S:
                overdue.append(_alert("a2", "overdue", subject, round(max(0.0, -left), 3), "high"))
        elif state in BAD_ASK_STATES and row.get("note") != "other generation":  # A4: no alert at all for another lineage
            ended = finite_number(row.get("ended_at"), overflow_is_missing=True)
            if ended is not None and now - ended <= ALERT_WINDOW_S:
                ended_badly.append(_alert("a1", str(state), subject, row.get("age_s"), "high"))
    login: list[dict[str, Any]] = []
    expiring: list[dict[str, Any]] = []
    for account in accounts or []:
        peer = str(account.get("peer_id") or "?")
        if account.get("logged_in") is False:
            login.append(_alert("a5", "login", peer, None, "high"))
            continue
        expires = finite_number(account.get("login_token_expires_at"), overflow_is_missing=True)
        if expires is not None and expires <= now:
            login.append(_alert("a5", "token_expired", peer, round(now - expires, 3), "high"))
        elif expires is not None and expires - now <= TOKEN_EXPIRY_WARN_S:
            expiring.append(_alert("a5", "token_expiring", peer, None, "low"))
    out_pools = pool_out_alerts(str(p["pool"]) for p in pools or [] if p.get("state") == "out")
    facts: list[dict[str, Any]] = []
    if isinstance(facts_report, dict) and facts_report.get("status") == "needs_recheck":
        raw_reasons: object = facts_report.get("reasons")
        reasons = [str(r) for r in cast(list[object], raw_reasons)] if isinstance(raw_reasons, list) else []
        facts.append(_alert("a6", "facts", fit_line(", ".join(reasons) or "needs_recheck", 120), None, "low"))
    return failed + gone + overdue + ended_badly + login + out_pools + expiring + facts


def _saved_defaults_source(groups: list[str]) -> str | None:
    """The saved-defaults layer when `groups` is exactly what it selects; the read model has no other view of the selection."""
    try:
        from peerhub.extensions.monitor_preferences import effective_preferences
        preference = effective_preferences()
    except (ImportError, OSError, ValueError, KeyError, TypeError):
        return None
    if preference.get("status") == "OK" and preference.get("source") in ("workspace", "global") \
            and list(preference.get("selected_groups") or []) == list(groups):
        return str(preference["source"])
    return None


def alert_source_status(groups: list[str], errors: dict[str, str], activity_data: dict[str, Any] | None,
                        quota: QuotaSnapshot, pools: list[dict[str, Any]] | None,
                        preference_source: str | None) -> dict[str, Any]:
    """Report how completely each alert source was read and whether saved defaults omitted activity."""
    capacity_read = bool(set(groups) & {"capacity", "peers"})
    capacity_failed = "capacity" in errors

    no_evidence = capacity_read and quota.never_refreshed  # never a quota refresh: not read, but not a store failure

    def source(selected: bool, failed: bool, cut: bool) -> str:
        return "not_selected" if not selected else "error" if failed else "truncated" if cut else "read"

    def quota_source(state: str) -> str:
        return "no_evidence" if no_evidence else state
    capacity_state = source(capacity_read, capacity_failed, quota.truncated)
    alert_sources: dict[str, Any] = {
        # Only the scope cap weakens the source: superseded rows never alert, so their cap stays in data.truncated.
        "activity": source("activity" in groups, "activity" in errors or bool(activity_data and activity_data["reason"]),
                           bool(activity_data and activity_data["scopes_total"] > ACTIVITY_SCOPE_CAP)),
        "capacity": quota_source(capacity_source(pools) if capacity_state == "read" and pools is not None else capacity_state),
        "accounts": quota_source(source(capacity_read, capacity_failed, quota.accounts_truncated)),
        "facts": quota_source(source(capacity_read, capacity_failed, False)),
    }
    if no_evidence:
        alert_sources["reasons"] = {"capacity": "no quota evidence"}
    if "activity" not in groups and (preference_source or _saved_defaults_source(groups)) in ("workspace", "global"):
        alert_sources["hint"] = "peerhub monitor defaults reset"
    return alert_sources
