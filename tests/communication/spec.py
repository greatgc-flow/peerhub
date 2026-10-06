"""Paths/loaders for the frozen M1 spec package (test-side only)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "docs" / "m1_spec"
TESTSET = SPEC / "06_GUIDES" / "TEST_SET"
SCHEMAS = SPEC / "04_SCHEMAS"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def catalog() -> list[dict]:
    return load(TESTSET / "test-catalog.json")["tests"]


def catalog_ids() -> set[str]:
    return {t["id"] for t in catalog()}


def schema(name: str) -> dict:
    return load(SCHEMAS / f"{name}.schema.json")


# --- independent JSON Schema helpers (Draft 2020-12 + strict RFC 3339 date-time) ---
import re
from datetime import datetime

from jsonschema import FormatChecker
from jsonschema.validators import Draft202012Validator

_RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$")
FORMATS = FormatChecker()


@FORMATS.checks("date-time")
def _date_time(v: object) -> bool:
    if not isinstance(v, str):
        return True
    if not _RFC3339.match(v):
        return False
    try:
        datetime.fromisoformat(v.replace("z", "Z"))
    except ValueError:
        return False
    return True


def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(schema(name), format_checker=FORMATS)


def errors(name: str, obj: object) -> list:
    return list(validator(name).iter_errors(obj))


def example(name: str) -> dict:
    return load(SCHEMAS / "examples" / f"{name}.example.json")


def valid_objects() -> dict[str, dict]:
    """One minimal valid object per public schema."""
    return {
        "peer": {"schema_version": "1.0", "peer_id": "p-1", "created_at": "2026-10-01T00:00:00Z"},
        "stream": {"schema_version": "1.0", "stream_id": "s-1", "state": "OPEN", "members": ["p-1"],
                   "revision": 1, "created_at": "2026-10-01T00:00:00Z"},
        "record": example("record"),
        "offset": {"schema_version": "1.0", "peer_id": "p-1", "stream_id": "s-1",
                   "read_through_position": 0, "revision": 1},
        "peer-observation": example("peer-observation"),
        "resource-pool": {"schema_version": "1.0", "resource_pool_id": "pool-1", "provider": "x", "kind": "QUOTA"},
    }
