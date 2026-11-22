"""Quota freshness vocabulary shared by the CLI (composition root) and the observation extension.

A neutral, dependency-free core module: the monitor's refresh scheduling and the freshness policy must read ONE number, and
it must stay importable when the first-party extensions are disabled (core-only mode).
"""

from __future__ import annotations

LOW_QUOTA_FRACTION = 0.10  # a pool with this fraction left or less is LOW in the monitor, diag and suggest (one source)
QUOTA_TTL_SECONDS = 300  # a quota reading is STALE (gray) at this age; the monitor refreshes before it, see MONITOR_REFRESH_LEAD_SECONDS
