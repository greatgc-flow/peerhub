"""Strict wire boundary for Core objects: JSON Schema (Draft 2020-12) validation before model construction.

Schemas in ./schemas are byte copies of docs/m1_spec/04_SCHEMAS (drift guarded by tests/communication/schema).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import FormatChecker
from jsonschema.validators import Draft202012Validator

from .models import Offset, Peer, Record, Stream, compute_record_digest, is_rfc3339

_DIR = Path(__file__).parent / "schemas"
_MODELS = {"peer": Peer, "stream": Stream, "record": Record, "offset": Offset}
_FORMATS = FormatChecker()


def _check_date_time(v: object) -> bool:
    return is_rfc3339(v) if isinstance(v, str) else True


_FORMATS.checks("date-time")(_check_date_time)


class WireValidationError(ValueError):
    """Wire object violates the published schema; nothing was persisted."""


@lru_cache(maxsize=None)
def _validator(kind: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((_DIR / f"{kind}.schema.json").read_text(encoding="utf-8")), format_checker=_FORMATS)


def parse_wire(kind: str, obj: Any) -> Any:
    if kind not in _MODELS:
        raise WireValidationError(f"unknown core kind {kind!r}")
    errs = sorted(_validator(kind).iter_errors(obj), key=lambda e: list(e.path))  # pyright: ignore[reportUnknownMemberType]  # jsonschema stubs leave iter_errors partially unknown
    if errs:
        raise WireValidationError("; ".join(f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errs[:5]))
    return _MODELS[kind].model_validate(obj)


def append_request_from_wire(rec: Record) -> dict[str, Any]:
    """Wire Record -> client append request (TD-21: server-owned fields are dropped, never trusted).

    The wire payload_digest must equal the TD-02 digest of the semantic fields, else the object is rejected.
    """
    req = {k: getattr(rec, k) for k in ("stream_id", "author_peer_id", "kind", "body", "targets", "reply_to", "refs",
                                       "metadata", "idempotency_key", "created_at")}
    if compute_record_digest(req) != rec.payload_digest:
        raise WireValidationError("payload_digest does not match canonical semantic payload")
    return req
