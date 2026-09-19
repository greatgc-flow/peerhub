"""Canonical SQLite table-name constants.

NOT used inside the migrations/ SQL files themselves -- those are an
immutable historical record of exactly what schema each version applied,
and must keep their literal table names verbatim forever, even if a name
were to (hypothetically) change here. This module is only for Python code
in this package that references an existing table by name in a query it
constructs (JOIN/FROM/UPDATE targets), to stop that literal from drifting
independently across call sites.
"""

from __future__ import annotations

TABLE_DISPATCH_REQUESTS = "dispatch_requests"
TABLE_DISPATCH_ATTEMPTS = "dispatch_attempts"
TABLE_EFFECT_DELIVERIES = "effect_deliveries"
TABLE_EFFECT_RECEIPTS = "effect_receipts"
TABLE_CONSUMER_OFFSETS = "consumer_offsets"
