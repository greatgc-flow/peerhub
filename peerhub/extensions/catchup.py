"""Bounded catch-up projection (TD-12, TD-22).

A fresh session generation receives an ordered, bounded projection of the Stream instead of the whole transcript.
The projection is the longest contiguous ordered SUFFIX of the candidate window `(after_position, before_position)` that fits the
injected `CatchUpBudget`; it never reorders or skips inside the window. Truncation is explicit metadata (`boundary()`).

Size accounting (documented contract): a Record is serialized as canonical JSON of
`{position, record_id, author_peer_id, kind, body}`; the projection payload is the items joined by "," so
`used_bytes = sum(len(item)) + (n - 1)` UTF-8 bytes for n > 0 and 0 for the empty projection. Tokens are
`sum(token_estimator(item))` with the estimator injected (it is RuntimeTarget-specific, TD-12 Option).
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


def _check_count(name: str, v: Any) -> None:
    if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0):
        raise ValueError(f"{name} must be a non-negative int or None, got {v!r}")


@dataclass(frozen=True)
class CatchUpBudget:
    max_records: int | None = None
    max_bytes: int | None = None
    max_tokens: int | None = None
    token_estimator: Callable[[str], int] | None = None

    def __post_init__(self) -> None:
        for n in ("max_records", "max_bytes", "max_tokens"):
            _check_count(n, getattr(self, n))
        if self.max_tokens is not None and self.token_estimator is None:
            raise ValueError("max_tokens requires an injected token_estimator")

    def describe(self) -> dict:
        return {"max_records": self.max_records, "max_bytes": self.max_bytes, "max_tokens": self.max_tokens}


def item_json(record: Any) -> str:
    return json.dumps({"position": record.position, "record_id": record.record_id, "author_peer_id": record.author_peer_id,
                       "kind": record.kind, "body": record.body}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class CatchUpProjection:
    records: tuple
    truncated: bool
    omitted_count: int
    omitted_through_position: int | None
    first_position: int | None
    last_position: int | None
    candidates: int
    used_bytes: int
    used_tokens: int
    after_position: int
    before_position: int | None
    budget: dict = field(default_factory=dict)

    @property
    def used_records(self) -> int:
        return len(self.records)

    def payload(self) -> str:
        return ",".join(item_json(r) for r in self.records)

    def boundary(self) -> dict:
        """JSON-able boundary/truncation metadata (also persisted in context.boundary Records)."""
        return {"after_position": self.after_position, "before_position": self.before_position, "candidates": self.candidates,
                "included_count": len(self.records), "first_position": self.first_position, "last_position": self.last_position,
                "omitted_count": self.omitted_count, "omitted_through_position": self.omitted_through_position,
                "truncated": self.truncated, "used_bytes": self.used_bytes, "used_tokens": self.used_tokens, "budget": dict(self.budget)}


class ProjectedRecords(list):
    """The list handed to RuntimeTarget.deliver(): the projected Records plus `.boundary` metadata."""

    boundary: dict | None = None


def project_catch_up(records: Iterable[Any], budget: CatchUpBudget, *, after_position: int = 0,
                     before_position: int | None = None) -> CatchUpProjection:
    window: deque = deque()  # (record, bytes, tokens); only ever holds a suffix that fits the budget
    est = budget.token_estimator
    nbytes = ntokens = candidates = omitted = 0
    omitted_through: int | None = None
    last_seen = None
    for r in records:
        if last_seen is not None and r.position <= last_seen:
            raise ValueError("records must be strictly increasing by position")
        last_seen = r.position
        if r.position <= after_position or (before_position is not None and r.position >= before_position):
            continue
        candidates += 1
        item = item_json(r)
        b, t = len(item.encode("utf-8")), (est(item) if est is not None else 0)
        window.append((r, b, t))
        nbytes += b + (1 if len(window) > 1 else 0)
        ntokens += t

        def over() -> bool:
            return ((budget.max_records is not None and len(window) > budget.max_records)
                    or (budget.max_bytes is not None and nbytes > budget.max_bytes)
                    or (budget.max_tokens is not None and ntokens > budget.max_tokens))

        while window and over():  # drop the OLDEST until the suffix fits (the newest may itself be dropped: no gaps)
            old, ob, ot = window.popleft()
            nbytes -= ob + (1 if window else 0)
            ntokens -= ot
            omitted += 1
            omitted_through = old.position
    recs = tuple(r for r, _, _ in window)
    return CatchUpProjection(recs, omitted > 0, omitted, omitted_through, recs[0].position if recs else None,
                             recs[-1].position if recs else None, candidates, nbytes if recs else 0, ntokens, after_position,
                             before_position, budget.describe())


def build_catch_up(store: Any, stream_id: str, budget: CatchUpBudget, *, after_position: int = 0,
                   before_position: int | None = None, page: int = 200) -> CatchUpProjection:
    """Read the Stream in pages (TD-22, exclusive of the cursor) and project; memory stays bounded by the budget."""
    if isinstance(page, bool) or not isinstance(page, int) or page <= 0:
        raise ValueError("page must be a positive int")

    def pages():
        cursor = after_position
        while True:
            batch = store.read_records(stream_id, cursor, page)
            if not batch:
                return
            for r in batch:
                if before_position is not None and r.position >= before_position:
                    return
                yield r
            cursor = batch[-1].position

    return project_catch_up(pages(), budget, after_position=after_position, before_position=before_position)
