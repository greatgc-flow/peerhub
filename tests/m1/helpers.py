"""Shared Wave-1 test helpers (test-side only)."""
from __future__ import annotations

CONTROL_KINDS = ("control.pause", "control.resume", "control.redirect", "control.cancel", "context.boundary")


def seed(h, peers=("a", "b"), stream="s", members=None):
    for p in peers:
        h.create_peer({"peer_id": p})
    return h.create_stream({"stream_id": stream, "members": list(peers if members is None else members)})


def req(body="x", key="k", author="a", stream="s", **extra):
    return dict(stream_id=stream, author_peer_id=author, kind="message", body=body, idempotency_key=key, **extra)


def append_n(h, n, stream="s", author="a", prefix="r"):
    return [h.append_record(req(body=i, key=f"{prefix}{i}", stream=stream, author=author)) for i in range(n)]
