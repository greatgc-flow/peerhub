"""Machine resource readings and thresholds for planning and starting parallel asks."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping

import psutil

CLI_PEERS = ("cc", "cx", "ag")  # always shown; API peers appear only while active
MIN_FREE_RAM_GB = 6
CPU_BUSY_WARN_PCT = 80
CPU_SAMPLE_SECONDS = 0.25
BUSY_PROBE_TIMEOUT_FACTOR = 2.0
LOAD_WARNING_MESSAGES = {
    "free_ram": "free RAM {free:.1f} GB is below minimum {minimum} GB",
    "cpu_busy": "CPU load {busy:.1f}% is at or above warning level {warning}%",
}
PLAN_NA_POOLS_LABEL = "n/a pools"
PLAN_NA_POOLS_LIMIT = 4
MINE, OTHER, UNKNOWN = CALLER_RELATIONS = ("mine", "other", "unknown")
_GB = 1024 ** 3


@dataclass(frozen=True)
class ResourceSample:
    ram_available_bytes: int | None = None
    cpu_busy_pct: float | None = None
    cpu_logical_cores: int | None = None

    def facts(self) -> dict[str, Any]:
        ram = None if self.ram_available_bytes is None else self.ram_available_bytes / _GB
        return {
            "ram_free_gb": None if ram is None else round(ram, 1),
            "ram_min_gb": MIN_FREE_RAM_GB,
            "ram_ok": None if ram is None else ram >= MIN_FREE_RAM_GB,
            "cpu_busy_pct": self.cpu_busy_pct,
            "cpu_busy_warn_pct": CPU_BUSY_WARN_PCT,
            "cpu_logical_cores": self.cpu_logical_cores,
        }


def free_ram_bytes() -> int | None:
    """Available RAM, including reclaimable cache, when the OS reports it."""
    try:
        available = getattr(psutil.virtual_memory(), "available", None)
        return available if isinstance(available, int) else None
    except Exception:  # OS failures leave the reading unknown
        return None


def read_resources() -> ResourceSample:
    try:
        # With the default percpu=False, psutil returns one system-wide percentage.
        busy = float(psutil.cpu_percent(interval=CPU_SAMPLE_SECONDS))
    except (OSError, psutil.Error):
        busy = None
    try:
        cores = psutil.cpu_count()
    except (OSError, psutil.Error):
        cores = None
    return ResourceSample(free_ram_bytes(), busy, cores or os.cpu_count())


def probe_timeout_factor(resource_reader: Callable[[], ResourceSample] | None = None) -> float:
    busy = (resource_reader or read_resources)().cpu_busy_pct
    return BUSY_PROBE_TIMEOUT_FACTOR if busy is not None and busy >= CPU_BUSY_WARN_PCT else 1.0


def load_warnings(load: Mapping[str, Any]) -> list[str]:
    warnings: list[str] = []
    if load["ram_ok"] is False:
        warnings.append(LOAD_WARNING_MESSAGES["free_ram"].format(free=load["ram_free_gb"], minimum=load["ram_min_gb"]))
    busy = load["cpu_busy_pct"]
    if busy is not None and busy >= load["cpu_busy_warn_pct"]:
        warnings.append(LOAD_WARNING_MESSAGES["cpu_busy"].format(busy=busy, warning=load["cpu_busy_warn_pct"]))
    return warnings


def load_snapshot(active_by_peer: Mapping[str, int] | None, resources: ResourceSample,
                  by_pool: Mapping[str, int] | None = None, *, unmapped: int = 0,
                  active_by_relation: Mapping[str, int] | None = None) -> dict[str, Any]:
    """Lease-active counts and machine resources. Unavailable lease counts are None."""
    known = active_by_peer is not None
    counts: Mapping[str, int] = active_by_peer or {}
    active = sum(counts.values()) if known else None
    peers: dict[str, dict[str, int | None]] = {}
    for peer in dict.fromkeys([*CLI_PEERS, *counts]):
        count = counts.get(peer, 0) if known else None
        if peer in CLI_PEERS or count:
            peers[peer] = {"active": count}
    return {"active": active, "peers": peers, **resources.facts(),
            "by_pool": dict(by_pool) if known and by_pool is not None else None,
            "unmapped": unmapped if known and by_pool is not None else None,
            "active_by_relation": {
                relation: active_by_relation.get(relation, 0) if known and active_by_relation is not None else None
                for relation in CALLER_RELATIONS
            }}


def format_active_split(counts: Mapping[str, int | None] | None) -> str:
    """The optional ownership suffix shared by plan and batch load summaries."""
    if counts is None or not (counts.get(OTHER) or counts.get(UNKNOWN)):
        return ""
    return " (" + ", ".join(f"{relation} {counts[relation]}" for relation in CALLER_RELATIONS) + ")"


def format_load(load: Mapping[str, Any]) -> str:
    """Show current lease counts and machine readings in one line."""
    def count(value: Any) -> str:
        return "?" if value is None else str(value)

    peers = " ".join(f"{p} {count(v['active'])}" for p, v in load["peers"].items())
    ram = "?" if load["ram_free_gb"] is None else f"{load['ram_free_gb']:.1f}"
    cpu = "?" if load["cpu_busy_pct"] is None else f"{load['cpu_busy_pct']:.0f}%"
    pools = load.get("by_pool")
    unmapped = load.get("unmapped") or 0
    flight_parts = ["in flight"]
    if pools:
        flight_parts.append(", ".join(f"{p} {n}" for p, n in sorted(pools.items())))
    if unmapped > 0:
        flight_parts.append(f"unmapped {unmapped}")
    flight = " ".join(flight_parts) + " | " if len(flight_parts) > 1 else ""
    split = format_active_split(load.get("active_by_relation"))
    return (f"load: active {count(load['active'])}{split} | {peers} | "
            f"{flight}free RAM {ram} GB (min {load['ram_min_gb']}) | "
            f"CPU {cpu} of {count(load['cpu_logical_cores'])} (warn {load['cpu_busy_warn_pct']}) | sampled now")
