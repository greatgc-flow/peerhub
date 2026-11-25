"""Window identity, account-scoped parentage, and retirement from quota evidence."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, cast

from peerhub.core.text import clean_text
from peerhub.core.scalars import finite_number
from peerhub.extensions.observation_model import ABSENT_FACT_REASONS, DISABLED_REASON, NOT_REPORTED_REASON
from peerhub.extensions.quota_projection import scoped_parent_ref

WEEK_SECONDS = 7 * 86400
UNKNOWN_WINDOW_SECONDS = 999999999
EXPIRED_WINDOW_REASON = "quota window expired"


def parse_window(pool_ref: str) -> tuple[int, str]:
    token = pool_ref.split(":")[-1] if ":" in pool_ref else pool_ref
    part = token.split("-")[-1] if "-" in token else token
    match = re.match(r"^(\d+)([a-zA-Z]+)$", part)
    if match:
        num, unit = int(match.group(1)), match.group(2).upper()
        mult = 3600 if unit == "H" else 86400 if unit == "D" else 60 if unit == "M" else 1
        return (num * mult, f"{num}{unit}")
    return (UNKNOWN_WINDOW_SECONDS, token)


FAMILIES = {"G": "flash", "A": "argon", "3P": "claude-via-ag", "C": "claude",
            "F": "fable", "X": "codex"}


def pool_family(pool: str) -> str:
    family, _, window = pool.partition("-")
    return family if family in FAMILIES and re.fullmatch(r"[0-9]+[HD]", window) else "?"


def quota_window_remaining(item: Mapping[str, Any], read_at: float) -> float | None:
    """Seconds until the reading's reset or maximum window age, whichever comes first."""
    pool = item.get("pool_ref") or item.get("resource_pool_ref")
    if (not pool or item.get("kind", "quota") != "quota"
            or item.get("stored_state", item.get("state")) not in {"MEASURED", "STALE"}):
        return None
    raw = item.get("last_measurements") or item.get("measurements") or item.get("payload")
    if not isinstance(raw, Mapping):
        return None
    measurements = cast(Mapping[str, object], raw)
    remaining = finite_number(measurements.get("remaining_fraction"))
    if remaining is None or not 0 <= remaining <= 1:
        return None
    reset = finite_number(item.get("resets_at"))
    if reset is None:
        reset = finite_number(measurements.get("resets_at"))
    if reset is None:
        countdown = finite_number(item.get("reset_in_seconds"))
        if countdown is not None:
            reset = read_at + countdown
    age = finite_number(item.get("age_seconds"))
    window, _ = parse_window(str(pool))
    limits = [reset - read_at] if reset is not None else []
    if age is not None and 0 < window < UNKNOWN_WINDOW_SECONDS:
        limits.append(window - age)
    return min(limits, default=None)


def scoped_weekly_parents(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Fable's scoped weekly window depends on the all-models window when configured consumers confirm it.

    As in the detailed quota tree, require a strict consumer-set inclusion. Equal sets, duplicate display names
    and different account identities cannot establish a parent. This is presentation only, never a quota rewrite.
    """
    parents: dict[int, dict[str, Any]] = {}
    for child in rows:
        parent_ref = scoped_parent_ref(str(child["pool_ref"]))
        if parent_ref is None or child["retired"]:
            continue
        parent_name = parent_ref.rsplit(":", 1)[-1]
        common = [row for row in rows if row["peer"] == child["peer"] and row["pool"] == parent_name
                  and not row["retired"]]
        same_name = [row for row in rows if row["peer"] == child["peer"] and row["pool"] == child["pool"]
                     and not row["retired"]]
        if len(common) != 1 or common[0]["pool_ref"] != parent_ref or len(same_name) != 1:
            continue
        parent = common[0]
        members = set(child["profiles"])
        all_members = set(parent["profiles"])
        parent_ref, child_ref = str(parent.get("pool_ref") or ""), str(child.get("pool_ref") or "")
        if (members and members < all_members and parent_ref and child_ref
                and parent_ref.rpartition(":")[0] == child_ref.rpartition(":")[0]):
            parents[id(child)] = parent
    return parents


def omitted_plan_windows(observations: list[dict[str, Any]], plans: Mapping[str, str],
                         elapsed: float = 0) -> dict[tuple[str, str], dict[str, Any]]:
    """Retire stale windows behind a newer successful quota report from the same account and source."""
    def account_key(item: dict[str, Any]) -> tuple[str, str]:
        ref = str(item.get("pool_ref") or "")
        return str(item.get("peer_id")), ref.rpartition(":")[0]

    latest: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in observations:
        if item.get("kind", "quota") != "quota" or finite_number(item.get("effective_at_us")) is None:
            continue
        account = account_key(item)
        previous = latest.get(account, [])
        if not previous or item["effective_at_us"] > previous[0]["effective_at_us"]:
            latest[account] = [item]
        elif item["effective_at_us"] == previous[0]["effective_at_us"]:
            previous.append(item)
    replacements: dict[tuple[str, str], dict[str, Any]] = {}
    for item in observations:
        peer = str(item.get("peer_id"))
        report = latest.get(account_key(item), [])
        unscoped = latest.get((peer, ""), [])
        if report and unscoped and unscoped[0]["effective_at_us"] >= report[0]["effective_at_us"]:
            continue
        if (peer not in plans or not report or item.get("state") != "STALE" or not item.get("pool_ref")
                or clean_text(item.get("reason") or "").startswith(DISABLED_REASON)):
            continue
        source = item.get("source")
        if not source or any(row.get("source") != source for row in report):
            continue
        successful = all(row.get("pool_ref") and (row.get("state") == "MEASURED" or
                         (row.get("state") == "ABSENT" and clean_text(row.get("reason") or "").startswith(ABSENT_FACT_REASONS)))
                         and (finite_number(row.get("age_seconds")) or 0) + elapsed < (finite_number(row.get("ttl_seconds")) or 0)
                         for row in report)
        at = finite_number(item.get("effective_at_us"))
        if not successful or at is None or at >= report[0]["effective_at_us"]:
            continue
        replacements[(peer, str(item["pool_ref"]))] = {**item, "state": "ABSENT", "stored_state": "ABSENT",
            "reason": f"{NOT_REPORTED_REASON}; plan={plans[peer]}", "display_plan": plans[peer], "display_retired": True,
            "age_seconds": report[0].get("age_seconds"), "ttl_seconds": report[0].get("ttl_seconds"),
            "measurements": None, "last_measurements": None, "projection": {}, "resets_at": None, "reset_in_seconds": None}
    return replacements
