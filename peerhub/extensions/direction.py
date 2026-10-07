"""Redirect vs new direction (LONG_HORIZON_CONTINUITY.md, CTL-004/005).

Same goal -> same Stream (a `control.redirect` Record). Different goal -> a NEW Stream that carries an explicit prior reference
(`metadata.prior_stream_id`); the old Stream stays as history/reference (closed by default, `close_prior=False` keeps it open).
The goal identity is `Stream.metadata["goal"]` (exact string equality; no fuzzy matching). Core API only; replay is idempotent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from peerhub.core.models import AppendRequest, Stream
from peerhub.core.store import CasMismatchError


class DirectionError(ValueError):
    """The direction cannot be decided or conflicts with existing state; nothing was written."""


@dataclass(frozen=True)
class DirectionResult:
    decision: str  # REDIRECT | NEW_STREAM
    stream_id: str
    record: Any
    prior_stream_id: str | None


def decide_direction(current_goal: str | None, proposed_goal: str | None) -> str:
    for name, g in (("current", current_goal), ("proposed", proposed_goal)):
        if not isinstance(g, str) or not g:
            raise DirectionError(f"{name} goal is missing; the direction cannot be decided")
    return "REDIRECT" if current_goal == proposed_goal else "NEW_STREAM"


def apply_direction(store: Any, stream_id: str, *, author_peer_id: str, proposed_goal: str, instruction: Any, idempotency_key: str,
                    created_at: str, new_stream_id: str | None = None, close_prior: bool = True) -> DirectionResult:
    cur = store.get_stream(stream_id)
    if cur is None:
        raise DirectionError(f"unknown stream {stream_id!r}")
    decision = decide_direction(cur.metadata.get("goal"), proposed_goal)
    if decision == "REDIRECT":
        rec = store.append_record(stream_id=stream_id, author_peer_id=author_peer_id, kind="control.redirect",
                                  body={"goal": proposed_goal, "instruction": instruction}, idempotency_key=idempotency_key,
                                  created_at=created_at)
        return DirectionResult(decision, stream_id, rec, None)
    if not new_stream_id:
        raise DirectionError("a new goal needs an explicit new_stream_id")
    existing = store.get_stream(new_stream_id)
    want = {"goal": proposed_goal, "prior_stream_id": stream_id}
    if existing is not None and existing.metadata != want:
        raise DirectionError(f"stream {new_stream_id!r} already exists with a different goal/prior reference")
    # validate EVERYTHING before the first write: an invalid request must leave no new Stream behind (D-W4-6)
    if author_peer_id not in cur.members:
        raise DirectionError(f"author {author_peer_id!r} is not a member of stream {stream_id!r}")
    try:
        AppendRequest.model_validate({"stream_id": new_stream_id, "author_peer_id": author_peer_id, "kind": "message", "body": instruction,
                                      "idempotency_key": idempotency_key, "created_at": created_at})
    except ValueError as e:
        raise DirectionError(f"invalid new-direction payload: {e}") from e
    if existing is None:
        store.create_stream(Stream(stream_id=new_stream_id, title=cur.title, members=list(cur.members), metadata=want))
    rec = store.append_record(stream_id=new_stream_id, author_peer_id=author_peer_id, kind="message", body=instruction,
                              idempotency_key=idempotency_key, created_at=created_at)
    if close_prior:
        for _ in range(8):
            cur = store.get_stream(stream_id)
            if cur.state.value == "CLOSED":
                break
            try:
                store.cas_stream(stream_id, cur.revision, {"state": "CLOSED", "metadata": {**cur.metadata, "superseded_by": new_stream_id}})
                break
            except CasMismatchError:
                continue
        else:
            raise DirectionError(f"could not close prior stream {stream_id!r} (CAS conflicts); {new_stream_id!r} exists with its first Record, "
                                 "retry is idempotent")
    return DirectionResult(decision, new_stream_id, rec, stream_id)
