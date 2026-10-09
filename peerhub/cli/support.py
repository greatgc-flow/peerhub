"""Small helpers shared by the CLI commands that run the quota collector (observation refresh, monitor)."""
from __future__ import annotations

import sys
from typing import Any


def print_warnings(result: dict[str, Any]) -> None:
    for w in result.get("warnings", []):
        print(f"warning: {w}", file=sys.stderr, flush=True)


def agy_token_use(result: dict[str, Any]) -> bool:
    return any(o.get("payload", {}).get("evidence_ref") == "agy_usage_consumed_tokens" for o in result.get("observations", []))
