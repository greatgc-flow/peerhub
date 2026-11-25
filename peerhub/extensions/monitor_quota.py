"""Read and summarize quota, account, and capacity evidence for the monitor."""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator, cast

from peerhub.core.quota_policy import LOW_QUOTA_FRACTION
from peerhub.extensions.monitor_queries import (
    ACCOUNT_ROW_CAP,
    capacity_rows,
    last_measured_account_rows,
    latest_account_quota_row,
    no_observation_tables,
    quota_age_rows,
    quota_evidence_rows,
)
from peerhub.extensions.monitor_values import finite_number
from peerhub.extensions.observation_model import (
    ABSENT_FACT_REASONS,
    MEASUREMENT_KEYS,
    NOT_REPORTED_REASON,
    FreshnessPolicy,
    ObservationCorruptError,
    account_text as _account_text,
    changed_account_plan,
    evaluate as evaluate_observation,
    row_to_observation,
    valid_epoch_seconds,
)
from peerhub.extensions.quota_projection import project_exhaustion
from peerhub.extensions.quota_windows import quota_window_remaining


def mapped_quota(configured_pools: list[str], peer_observations: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Peer quota over its profile's configured pools: the freshest row per pool, the most constrained wins.

    A pool the provider explicitly did not report (ABSENT "not reported by provider") is skipped, never
    limiting; if every configured pool is not reported the peer is UNKNOWN, never 100% free. Anything else
    that is not a fresh valid measurement makes the peer INCOMPLETE (fail closed)."""
    mapped_measured: list[tuple[float, dict[str, Any]]] = []
    not_reported_pools = 0
    for p_req in configured_pools:
        matching_obs = [
            o for o in peer_observations
            if (str(o.get("pool_ref") or o.get("pool_kind") or "").split(":")[-1]) == p_req
        ]
        if not matching_obs:
            return _incomplete_quota()
        best_matching = max(matching_obs, key=lambda o: int(o.get("effective_at_us") or 0))  # freshest, not first
        if (best_matching.get("state") == "ABSENT"
                and str(best_matching.get("reason") or "").startswith(ABSENT_FACT_REASONS)):
            not_reported_pools += 1  # the provider says this window does not exist or is disabled: never limiting
            continue
        raw_m_val = best_matching.get("measurements")
        q_m_dict = cast(dict[str, Any], raw_m_val) if isinstance(raw_m_val, dict) else {}
        rem_val = q_m_dict.get("remaining_fraction")
        is_valid = (
            isinstance(rem_val, (int, float)) and not isinstance(rem_val, bool)
            and math.isfinite(float(rem_val)) and 0.0 <= float(rem_val) <= 1.0
        )
        if best_matching.get("state") != "MEASURED" or not is_valid:
            return _incomplete_quota()
        mapped_measured.append((float(cast(float, rem_val)), best_matching))
    if mapped_measured:
        lowest_rem, best_obs = min(mapped_measured, key=lambda x: (x[0], str(x[1].get("pool_ref") or "")))
        return {
            "pool_ref": best_obs.get("pool_ref") or best_obs.get("pool_kind"),
            "remaining_fraction": lowest_rem,
            "percent": round(lowest_rem * 100),
            "state": "MEASURED",
            "source": best_obs.get("source"),
            "age_seconds": best_obs.get("age_seconds"),
            "scope": "mapped",
        }
    if not_reported_pools:
        return {
            "pool_ref": "not_reported",
            "remaining_fraction": None,
            "percent": None,
            "state": "UNKNOWN",
            "source": "derived",
            "age_seconds": None,
            "scope": "mapped",
        }
    return None


# One poller is one probe source even when its rows carry different refs: ag falls back from /usage to the statusline log inside
# a run, and a poller that raised is stored by refresh_quota as source `quota_probe:<kind>` without an evidence_ref.
# Deliberately NOT mapped (they stay visible after a later success): agy_usage_cwd_refused, agy_usage_consumed_tokens,
# agy_usage_unverifiable_tokens, agy_usage_not_a_command and probe:ag:conflict. They are safety signals about that run (a refusal,
# possible token spend, a vocabulary conflict), not transient probe failures.
_PROBE_SOURCE_FAMILY = {"probe:ag:statusline": "probe:ag:usage", "quota_probe:cx": "probe:cx:app-server",
                        "quota_probe:cc": "probe:cc:usage", "quota_probe:ag": "probe:ag:usage"}


def _probe_source(payload: dict[str, Any], source: str) -> str:
    ref = str(payload.get("evidence_ref") or "") or source
    return _PROBE_SOURCE_FAMILY.get(ref, ref)


def _superseded(item: dict[str, Any], newest_success_us: int) -> bool:
    """A peer-level (pool-less) quota failure is superseded by a strictly newer MEASURED pool row of the same peer (the account
    key: quota rows are stored per peer) and probe source. Successful probes write pool rows only, so they never replace the
    failure row themselves. Other sources, other peers and same-instant rows of one partial run never hide it; history is kept."""
    return (item["kind"] == "quota" and item["pool_ref"] is None and item["stored_state"] != "MEASURED"
            and newest_success_us > int(item["effective_at_us"]))


_ACCOUNT_TRI = {"reported", "n/a", "UNKNOWN"}
_ACCOUNT_ORDER = {"cc": 0, "cx": 1, "ag": 2}  # builtin peers first in the documented order, then the rest by id


def _account_item(observation: Any, freshness: Any, last_good: tuple[Any, Any] | None = None) -> dict[str, Any]:
    """One account observation -> the monitor's account fields. Only normalized stored fields are read (no identity exists to leak).

    A failed probe carries no account facts. When an earlier MEASURED reading exists (`last_good`: observation, freshness) it stays
    shown, forced to aged (STALE), with the failure as `probe_error`; otherwise every fact is UNKNOWN."""
    failed = freshness.stored_state.value != "MEASURED"
    probe_error = (_account_text(observation.payload.get("reason"), 40) or "unknown") if failed else None
    state_override = None
    if failed and last_good is not None:
        observation, freshness = last_good
        failed, state_override = False, "STALE"
    payload: dict[str, Any] = observation.payload
    ok = not failed
    login = payload.get("logged_in")
    expires = payload.get("login_token_expires_at")
    plan_status, expiry_status = payload.get("plan_status"), payload.get("login_expiry_status")
    plan = _account_text(payload.get("plan")) if ok and plan_status == "reported" else None
    expires_ok = ok and expiry_status == "reported" and valid_epoch_seconds(expires)  # re-validated: stored values may come from anywhere
    return {
        "peer_id": observation.subject_ref,
        "state": state_override or freshness.state.value,
        "stored_state": freshness.stored_state.value,
        "age_seconds": freshness.age_seconds,
        "ttl_seconds": freshness.ttl_seconds,
        "logged_in": login if ok and isinstance(login, bool) else "UNKNOWN",
        "plan": plan,
        "plan_status": "reported" if plan else (plan_status if ok and plan_status in {"n/a", "UNKNOWN"} else "UNKNOWN"),
        "login_token_expires_at": expires if expires_ok else None,
        "login_expiry_status": "reported" if expires_ok else ("n/a" if ok and expiry_status == "n/a" else "UNKNOWN"),
        "auth_method": _account_text(payload.get("auth_method")) if ok else None,
        "endpoint_access": "ok" if ok and payload.get("endpoint_access") == "ok" else None,
        "probe_error": probe_error,
    }


def _read_accounts(conn: sqlite3.Connection, latest_rows: list[sqlite3.Row], now: float) -> list[dict[str, Any]]:
    """Account items, builtin peers first. For a peer whose latest probe failed, one extra query fetches its last MEASURED reading."""
    def parsed(row: sqlite3.Row) -> tuple[Any, Any]:
        try:
            observation, capture_seq = row_to_observation(row)
        except ObservationCorruptError as exc:
            raise sqlite3.DataError(f"invalid {row['kind']} observation at capture_seq={row['capture_seq']}") from exc
        return observation, evaluate_observation(observation, capture_seq, round(now * 1_000_000), FreshnessPolicy())

    latest = [parsed(row) for row in latest_rows]
    failed = sorted({obs.subject_ref for obs, view in latest if view.stored_state.value != "MEASURED"})
    good: dict[str, tuple[Any, Any]] = {}
    if failed:
        for row in last_measured_account_rows(conn, failed):
            observation, view = parsed(row)
            good[observation.subject_ref] = (observation, view)
    items: list[dict[str, Any]] = []
    for obs, view in latest:
        item = _account_item(obs, view, good.get(obs.subject_ref))
        # Query the actual latest quota row, independently of the display's quota row cap.
        row = latest_account_quota_row(conn, obs.subject_ref)
        plan = changed_account_plan(obs, parsed(row)[0] if row is not None else None)
        if plan:
            item.update(plan=plan, plan_source="quota", plan_changed=True, state="STALE")
        items.append(item)
    return sorted(items, key=lambda a: (_ACCOUNT_ORDER.get(str(a["peer_id"]), len(_ACCOUNT_ORDER)), str(a["peer_id"])))


def _fill_unknown_plans(observations: list[dict[str, Any]], accounts: list[dict[str, Any]]) -> None:
    """A "not reported ... plan=unknown" pool note gets `display_plan` (+ `plan_source: "account"`) from a FRESH account observation of
    the same peer; the stored `reason` is never rewritten, a plan the quota probe itself reported is never replaced, and without a fresh
    account plan the note stays "unknown". The renderers read these fields, so every view agrees."""
    plans = {str(a["peer_id"]): str(a["plan"]) for a in accounts if a["state"] == "MEASURED" and a["plan_status"] == "reported" and a["plan"]}
    for item in observations:
        reason = item.get("reason")
        plan = plans.get(str(item["peer_id"]))
        if plan and item["pool_ref"] is not None and isinstance(reason, str) and reason.startswith(NOT_REPORTED_REASON) \
                and reason.endswith("plan=unknown"):
            item["display_plan"], item["plan_source"] = plan, "account"  # `reason` stays exactly as stored


def _quota_age_observations(conn: sqlite3.Connection, now: float) -> Iterator[dict[str, Any]]:
    """Supply quota age evidence for human frames without a capacity read."""
    read_us = round(now * 1_000_000)
    policy = FreshnessPolicy()
    for row in quota_age_rows(conn, read_us):
        try:
            observation, capture_seq = row_to_observation(row)
        except ObservationCorruptError:
            continue
        freshness = evaluate_observation(observation, capture_seq, read_us, policy)
        item = {"kind": observation.kind, "subject_ref": observation.subject_ref, "resource_pool_ref": observation.resource_pool_ref,
                "state": freshness.state.value, "stored_state": freshness.stored_state.value,
                "age_seconds": freshness.age_seconds, "payload": observation.payload}
        remaining = quota_window_remaining(item, now)
        if remaining is None or remaining >= 0:
            yield {**item, "payload": {"remaining_fraction": observation.payload.get("remaining_fraction")},
                   "expires_in": remaining}


def _incomplete_quota() -> dict[str, Any]:
    return {
        "pool_ref": "incomplete",
        "remaining_fraction": None,
        "percent": None,
        "state": "INCOMPLETE",
        "source": "derived",
        "age_seconds": None,
        "scope": "mapped",
    }


LastKnown = dict[str, tuple[float, float | None]]  # observation_id -> (validated fraction, resets_at) of a STALE row


def pool_states(observations: list[dict[str, Any]], last_known: LastKnown | None = None,
                now: float | None = None, samples: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """Display state of every quota pool row and its rounded percent left. A fresh (MEASURED) row is out, low, fast, ok or
    reset; a STALE row with a last-known fraction is `aged` (never out, low or fast, so a3 stays fresh-only), or `reset` once
    its resets_at passed; anything else is `?`. `fresh` marks a MEASURED row with a valid fraction; `na` marks a window the
    provider says it does not have or has disabled (ABSENT with one of ABSENT_FACT_REASONS, also when aged).

    `shown` is what the POOLS table shows (precedence n/a ? reset out low fast aged ok): `state` for a fresh row; for a STALE
    row out at 0, low at `LOW_QUOTA_FRACTION` or less, fast on a threat projection, else aged. `fast` needs two projection
    samples, as in the view (monitor_format.compact_pool): the item's `projection_samples`, else `samples[observation_id]`, else 0."""
    pools: list[dict[str, Any]] = []
    for item in observations:
        if item.get("kind") != "quota" or item.get("pool_ref") is None:
            continue
        pool = str(item.get("pool_ref")).split(":")[-1]
        evidence = item.get("state")
        if evidence == "STALE" and item.get("stored_state", "MEASURED") != "MEASURED":
            evidence = item.get("stored_state")  # an aged ABSENT or failed reading is not a stale measurement
        raw_m: object = item.get("measurements")
        measured = cast(dict[str, Any], raw_m) if isinstance(raw_m, dict) else {}
        remaining = finite_number(measured.get("remaining_fraction")) if evidence == "MEASURED" else None
        if remaining is not None and not 0 <= remaining <= 1:
            remaining = None
        fresh = remaining is not None
        raw_p: object = item.get("projection")
        projection = cast(dict[str, Any], raw_p) if isinstance(raw_p, dict) else {}
        reset_in = finite_number(item.get("reset_in_seconds"))
        count = finite_number(item.get("projection_samples"))
        if count is None:
            count = finite_number((samples or {}).get(str(item.get("observation_id"))))
        threat = projection.get("state") == "threat" and (count or 0) >= 2

        def graded(fraction: float, otherwise: str) -> str:
            return "out" if fraction == 0 else "low" if fraction <= LOW_QUOTA_FRACTION else "fast" if threat else otherwise
        known = (last_known or {}).get(str(item.get("observation_id"))) if evidence == "STALE" else None
        if known is not None:
            remaining, resets_at = known
            passed = resets_at is not None and now is not None and resets_at <= now
            state, remaining = ("reset", None) if passed else ("aged", remaining)
            shown = state if remaining is None else graded(remaining, "aged")
        elif remaining is None:
            state = shown = "?"
        elif reset_in is not None and reset_in <= 0:
            state, remaining = "reset", None
            shown = state
        else:
            state = shown = graded(remaining, "ok")
        pools.append({"pool": f"{item.get('peer_id')} {pool}", "left_pct": None if remaining is None else round(remaining * 100),
                      "state": state, "shown": shown, "window": pool.rpartition("-")[2] if "-" in pool else "?",
                      "evidence": evidence, "fresh": fresh,
                      "na": evidence == "ABSENT" and str(item.get("reason") or "").startswith(ABSENT_FACT_REASONS)})
    return pools


def capacity_source(pools: list[dict[str, Any]]) -> str:
    """The 7-day rows, without the windows a provider says it does not have (`na`): `read` when there is none or one is fresh
    (MEASURED with a valid fraction); else `stale` when at least one is STALE; else `unknown` (every one is unknown or in
    error, so no 7-day pool has a number)."""
    weekly = [p for p in pools if p.get("window") == "7D" and not p.get("na")]
    if not weekly or any(p.get("fresh") for p in weekly):
        return "read"
    return "stale" if any(p.get("evidence") == "STALE" for p in weekly) else "unknown"


def min_quota(pools: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The lowest 7-day pool with a known percent left: its shown state (as in the POOLS table) and whether it is stale."""
    weekly = [p for p in pools if p["window"] == "7D" and p["left_pct"] is not None]
    if not weekly:
        return None
    low = min(weekly, key=lambda p: (p["left_pct"], p["pool"]))
    return {"pool": low["pool"], "left_pct": low["left_pct"], "state": low.get("shown", low["state"]),
            "aged": low.get("evidence") == "STALE"}


@dataclass
class QuotaSnapshot:
    observations: list[dict[str, Any]]
    accounts: list[dict[str, Any]]
    facts_report: dict[str, Any] | None
    truncated: bool
    accounts_truncated: bool
    never_refreshed: bool
    last_known: LastKnown
    sample_counts: dict[str, int]


def read_quota_groups(conn: sqlite3.Connection, groups: list[str], output: dict[str, Any],
                      errors: dict[str, str], now: float) -> QuotaSnapshot:
    """Read quota and account evidence needed by the capacity and peers groups."""
    observations: list[dict[str, Any]] = []
    staged: list[tuple[tuple[str, str], dict[str, Any]]] = []  # (peer, probe source) with its row, in query order
    newest_success: dict[tuple[str, str], int] = {}  # newest MEASURED pool row per (peer, probe source)
    reset_credits: list[dict[str, Any]] = []
    accounts: list[dict[str, Any]] = []
    latest_facts_report: dict[str, Any] | None = None
    latest_facts_seq = -1  # capture_seq is store-wide: the newest report wins, not the first in query order (#366 M1b)
    capacity_truncated = accounts_truncated = False
    last_known: LastKnown = {}
    capacity_never_refreshed = False  # the observation tables do not exist yet: no quota evidence, not a store failure
    sample_counts: dict[str, int] = {}  # observation_id -> distinct MEASURED samples of its window (0..2)
    if bool(set(groups) & {"capacity", "peers"}):
        try:
            all_rows = capacity_rows(conn)
            rows = [row for row in all_rows if row["kind"] != "account"]
            account_rows = [row for row in all_rows if row["kind"] == "account"]
            for row in rows[:100]:
                try:
                    observation, capture_seq = row_to_observation(row)
                except ObservationCorruptError as exc:
                    raise sqlite3.DataError(f"invalid quota observation at capture_seq={row['capture_seq']}") from exc
                if capture_seq > latest_facts_seq and "facts_report" in observation.payload:
                    raw_f: object = observation.payload.get("facts_report")
                    if isinstance(raw_f, dict):
                        latest_facts_report, latest_facts_seq = cast(dict[str, Any], raw_f), capture_seq
                freshness = evaluate_observation(observation, capture_seq, round(now * 1_000_000), FreshnessPolicy())
                if observation.kind == "reset_credit":
                    shown = freshness.state.value in {"MEASURED", "STALE"}
                    reset_credits.append({
                        "peer_id": observation.subject_ref,
                        "state": freshness.state.value,
                        "age_seconds": freshness.age_seconds,
                        "available_count": observation.payload.get("available_count") if shown else None,
                        "nearest_expires_at": observation.payload.get("nearest_expires_at") if shown else None,
                    })
                    continue
                observed_at = observation.observed_at
                try:
                    measured_at = datetime.fromisoformat(observed_at.replace("Z", "+00:00")).timestamp()
                except (ValueError, OverflowError):
                    measured_at = None
                remaining = observation.payload.get("remaining_fraction")
                used_fraction = (1 - float(remaining)
                                 if isinstance(remaining, (int, float)) and not isinstance(remaining, bool)
                                 and math.isfinite(float(remaining)) and 0 <= remaining <= 1 else None)
                projection = project_exhaustion(
                    used_fraction, observation.payload.get("window_started_at"), measured_at,
                    observation.payload.get("resets_at"), now,
                    evidence_state=freshness.state.value,
                    anchor_basis=observation.payload.get("anchor_basis"),
                )
                measurements = None
                reset_in_seconds = None
                checked_remaining = finite_number(remaining) if used_fraction is not None else None  # the value validated above
                if freshness.state.value == "STALE" and checked_remaining is not None:  # alerts only: public value stays None
                    stale_resets: object = observation.payload.get("resets_at")
                    last_known[observation.observation_id] = (checked_remaining, finite_number(stale_resets))
                if row["projection_samples"] is not None:  # from the capacity statement itself, on every path
                    sample_counts[observation.observation_id] = int(row["projection_samples"])
                if freshness.state.value == "MEASURED":
                    resets_at = observation.payload.get("resets_at")
                    if (isinstance(resets_at, (int, float)) and not isinstance(resets_at, bool)
                            and math.isfinite(resets_at)):
                        reset_in_seconds = max(0.0, resets_at - now)
                    measured: dict[str, int | float] = {}
                    for key in MEASUREMENT_KEYS.intersection(observation.payload):
                        value = observation.payload[key]
                        if (isinstance(value, (int, float)) and not isinstance(value, bool) and
                                math.isfinite(float(value)) and value >= 0 and
                                (key != "remaining_fraction" or value <= 1)):
                            measured[key] = value
                    measurements = measured
                pool_ref = observation.resource_pool_ref
                probe = (str(observation.subject_ref), _probe_source(observation.payload, observation.source))  # account key = peer id
                if observation.kind == "quota" and pool_ref is not None and freshness.stored_state.value == "MEASURED":
                    newest_success[probe] = max(newest_success.get(probe, 0), int(row["effective_at_us"]))
                staged.append((probe, {
                    "observation_id": observation.observation_id,
                    "peer_id": observation.subject_ref,
                    "provider": row["provider"],
                    "kind": observation.kind,
                    "pool_ref": pool_ref,
                    "pool_kind": row["pool_kind"],
                    "attribution_scope": "peer_account_pool" if pool_ref is not None else "peer",
                    "source": observation.source,
                    "projection": projection,
                    "state": freshness.state.value,
                    "stored_state": freshness.stored_state.value,
                    "observed_at": observation.observed_at,
                    "captured_at": observation.captured_at,
                    "effective_at_us": int(row["effective_at_us"]),
                    "age_seconds": freshness.age_seconds,
                    "ttl_seconds": freshness.ttl_seconds,
                    "freshness_basis": freshness.basis,
                    "skew_seconds": freshness.skew_seconds,
                    "measurements": measurements,
                    "reset_in_seconds": reset_in_seconds,
                    "reason": observation.payload.get("reason") or observation.payload.get("condition"),
                }))
            observations = [item for probe, item in staged if not _superseded(item, newest_success.get(probe, 0))]
            capacity_truncated, accounts_truncated = len(rows) > 100, len(account_rows) > ACCOUNT_ROW_CAP
            accounts = _read_accounts(conn, account_rows[:ACCOUNT_ROW_CAP], now)
            _fill_unknown_plans(observations, accounts)
            if "capacity" in groups:
                output["capacity"] = {"status": "OK", "data": {"observations": observations,
                    "reset_credits": reset_credits,
                    **({"accounts": accounts} if accounts else {}),
                    **({"accounts_truncated": True} if len(account_rows) > ACCOUNT_ROW_CAP else {}),
                    "sample_size": min(len(rows), 100), "truncated": len(rows) > 100}}
        except sqlite3.Error as exc:
            capacity_never_refreshed = no_observation_tables(conn, exc)
            if not capacity_never_refreshed:
                errors["capacity"] = type(exc).__name__
            if "capacity" in groups:
                # No observation table at all is a capacity group read with nothing in it (as when the tables are
                # empty), not an error: the human frame and the JSON agree, and `alert_sources` says `no_evidence`.
                output["capacity"] = ({"status": "OK", "data": {"observations": [], "reset_credits": [],
                                                                "sample_size": 0, "truncated": False}}
                                      if capacity_never_refreshed else {"status": "ERROR", "data": None})
    return QuotaSnapshot(observations, accounts, latest_facts_report, capacity_truncated,
                         accounts_truncated, capacity_never_refreshed, last_known, sample_counts)


def read_human_quota(conn: sqlite3.Connection, groups: list[str], observations: list[dict[str, Any]], now: float
                     ) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Read the display-only quota evidence in the same transaction as the public snapshot."""
    human_observations: list[dict[str, Any]] = []
    quota_age: dict[str, list[dict[str, Any]]] = {}
    if not set(groups) & {"capacity", "peers"}:
        try:
            quota_age["_quota_age_readings"] = list(_quota_age_observations(conn, now))
        except sqlite3.Error as exc:
            if no_observation_tables(conn, exc):
                quota_age["_quota_age_readings"] = []
    if observations:
        # One batch within this read snapshot; redraws reuse these immutable facts.
        ids = [item["observation_id"] for item in observations]
        evidence = quota_evidence_rows(conn, ids, now)
        by_id = {row["observation_id"]: row for row in evidence}
        for item in observations:
            row = by_id.get(item["observation_id"])
            raw: dict[str, Any] = json.loads(row["payload_json"]) if row is not None else {}
            display = {**item, "last_measurements": raw, "resets_at": raw.get("resets_at"),
                       "projection_samples": row["samples"] if row else 0}
            if row is not None:
                display["_capture_seq"] = row["capture_seq"]
                if row["prior_at_us"] is not None:
                    prior: object = json.loads(row["prior_payload"])
                    if isinstance(prior, dict):
                        display["_prior_measurement"] = prior
                        display["_prior_age"] = now - row["prior_at_us"] / 1_000_000
            human_observations.append(display)
    return human_observations, quota_age
