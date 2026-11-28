"""Bounded, stateless source checkpoint. A checksum detects damage, not authorization."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import stat
from pathlib import Path
from typing import Any, cast

from peerhub.core.models import json_digest

MAX_STREAMS = 32
MAX_ID_BYTES = 256
MAX_TOKEN = 65536  # Accept previous draft cursors through their original limit.
MAX_EMITTED_TOKEN = 24576  # Leave room for the executable, DB path and options on Windows.
MAX_SEQ = 2**63 - 1


class ActivityError(ValueError):
    def __init__(self, reason: str, code: str = "invalid_arguments", exit_code: int = 2):
        super().__init__(reason)
        self.reason = reason
        self.code = code
        self.exit_code = exit_code


def identity(path: Path) -> tuple[str, str | None]:
    db_stat = path.stat()
    store = json_digest([str(path.resolve()), str(db_stat.st_dev), str(db_stat.st_ino)])
    markers: list[str | None] = []
    for name in ("workspace.generation", "restore.epoch"):
        marker = path.parent / name
        try:
            before = marker.lstat()
        except FileNotFoundError:
            markers.append(None)
            continue
        if not stat.S_ISREG(before.st_mode) or getattr(before, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ActivityError("SOURCE_UNAVAILABLE", "diag_unavailable", 5)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(marker, flags), "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(before, opened):
                raise ActivityError("RESNAPSHOT_REQUIRED")
            data = handle.read(129)
        if not data.strip() or len(data) > 128:
            raise ActivityError("RESNAPSHOT_REQUIRED")
        markers.append(hashlib.sha256(data).hexdigest())
    return store, json_digest(markers) if any(markers) else None


def wire(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _token(raw: bytes) -> str:
    return "a1." + base64.urlsafe_b64encode(hashlib.sha256(raw).digest() + raw).decode("ascii")


def encode(store: str, lineage: str, scope: str, after: dict[str, int], heads: dict[str, int] | None) -> str:
    token = _token(wire([1, store, lineage, scope, after, heads]).encode("ascii"))
    if len(token) <= MAX_EMITTED_TOKEN:
        return token
    # Store each partition once. Hex bounds each UTF-8 byte to two JSON characters,
    # including control characters that the original form escapes in both vectors.
    keys = sorted(after)
    encoded_keys = [key.encode("utf-8").hex() for key in keys]
    positions = [after[key] for key in keys]
    highs = [heads[key] for key in keys] if heads is not None else None
    token = _token(wire([2, store, lineage, scope, encoded_keys, positions, highs]).encode("ascii"))
    if len(token) > MAX_EMITTED_TOKEN:
        raise ActivityError("PARTITION_LIMIT")
    return token


def _vector(values: Any) -> dict[str, int]:
    if not isinstance(values, dict) or len(cast(dict[str, Any], values)) > MAX_STREAMS + 2:
        raise ValueError
    result: dict[str, int] = {}
    for key, n in cast(dict[str, Any], values).items():
        if (not (key in ("evidence", "outcome") or key.startswith("r:"))
                or len(key.encode()) > MAX_ID_BYTES + 2 or type(n) is not int or not 0 <= n <= MAX_SEQ):
            raise ValueError
        result[key] = n
    return result


def _json_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError
    return cast(list[Any], value)


def _unpack(payload: Any) -> tuple[object, object, object, dict[str, int], dict[str, int] | None]:
    version, origin, epoch, filters, *progress = _json_list(payload)
    if type(version) is not int:
        raise ValueError
    if version == 1:
        positions, highs = progress
    elif version == 2:
        encoded_keys, offsets, ceilings = progress
        encoded_keys = _json_list(encoded_keys)
        if len(encoded_keys) > MAX_STREAMS + 2:
            raise ValueError
        keys = [bytes.fromhex(key).decode("utf-8") for key in encoded_keys]
        if len(set(keys)) != len(keys):
            raise ValueError
        positions = dict(zip(keys, _json_list(offsets), strict=True))
        highs = None if ceilings is None else dict(zip(keys, _json_list(ceilings), strict=True))
    else:
        raise ValueError
    return origin, epoch, filters, _vector(positions), None if highs is None else _vector(highs)


def decode(token: str, store: str, lineage: str | None, scope: str) -> tuple[dict[str, int], dict[str, int] | None]:
    try:
        if not token or len(token) > MAX_TOKEN:
            raise ValueError
        data = base64.b64decode(token.removeprefix("a1."), altchars=b"-_", validate=True)
        raw = data[32:]
        if not hmac.compare_digest(data[:32], hashlib.sha256(raw).digest()):
            raise ValueError
        origin, epoch, filters, after, heads = _unpack(json.loads(raw))
        if heads is not None and (after.keys() != heads.keys() or any(after[k] > heads[k] for k in after)):
            raise ValueError
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise ActivityError("INVALID_CURSOR") from None
    if origin != store or not lineage or epoch != lineage:
        raise ActivityError("RESNAPSHOT_REQUIRED")
    if filters != scope:
        raise ActivityError("CURSOR_SCOPE_MISMATCH")
    return after, heads
