"""Shared text and JSON output for PeerHub commands."""
from __future__ import annotations

import json
from typing import Any, cast


def emit(payload: Any, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=True))
    elif isinstance(payload, dict):
        for key, value in cast(dict[str, object], payload).items():
            shown = json.dumps(value, ensure_ascii=True) if isinstance(value, (dict, list)) else "-" if value is None else value
            print(f"{key}: {shown}")
    elif isinstance(payload, list):
        for item in cast(list[object], payload):
            emit(item, False)
        if not payload:
            print("(no results)")
    else:
        print(payload)
