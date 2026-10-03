"""Strict wire boundary for Core objects: JSON Schema (Draft 2020-12) validation before model construction.

Schemas in ./schemas are byte copies of docs/m1_spec/04_SCHEMAS (drift guarded by tests/m1/schema).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import FormatChecker
from jsonschema.validators import Draft202012Validator

from .models import Offset, Peer, Record, Stream, is_rfc3339

_DIR = Path(__file__).parent / "schemas"
_MODELS = {"peer": Peer, "stream": Stream, "record": Record, "offset": Offset}
_FORMATS = FormatChecker()
_FORMATS.checks("date-time")(lambda v: is_rfc3339(v) if isinstance(v, str) else True)


class WireValidationError(ValueError):
    """Wire object violates the published schema; nothing was persisted."""


@lru_cache(maxsize=None)
def _validator(kind: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((_DIR / f"{kind}.schema.json").read_text(encoding="utf-8")), format_checker=_FORMATS)


def parse_wire(kind: str, obj: Any) -> Any:
    if kind not in _MODELS:
        raise WireValidationError(f"unknown core kind {kind!r}")
    errs = sorted(_validator(kind).iter_errors(obj), key=lambda e: list(e.path))
    if errs:
        raise WireValidationError("; ".join(f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errs[:5]))
    return _MODELS[kind].model_validate(obj)
