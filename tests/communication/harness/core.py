"""Core harness adapter over peerhub.core (CoreStore). Bridge/Observation/Diag harness ports arrive with their waves."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from peerhub.core.models import Offset, Peer, Record, Stream
from peerhub.core.store import CoreStore


def compute_state_digest(db_path) -> str:
    h = hashlib.sha256()
    with closing(sqlite3.connect(db_path)) as c:
        names = sorted(r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        for t in names:
            h.update(t.encode())
            for row in c.execute(f"SELECT * FROM {t} ORDER BY 1,2"):
                h.update(json.dumps(row, default=str).encode())
    return h.hexdigest()


class CoreHarness:
    def __init__(self, workspace: Path) -> None:
        from peerhub.core.workspace import Workspace

        self.ws = Workspace(Path(workspace))
        self.workspace = self.ws.root
        self.db_path = self.ws.db_path
        self.store = CoreStore(self.db_path)

    # --- identity / lifecycle
    def reopen(self) -> "CoreHarness":
        self.store = CoreStore(self.db_path)
        return self

    def workspace_generation(self) -> str:
        return self.ws.generation()

    def claims(self, clock):
        from peerhub.extensions.bridge_claims import ClaimStore

        return ClaimStore(self.db_path, generation=self.ws.generation, clock=clock.now)

    # --- Core ports
    def create_peer(self, peer_spec: dict[str, Any]) -> Peer:
        return self.store.register_peer(Peer.model_validate(peer_spec))

    def get_peer(self, peer_id: str) -> Peer | None:
        return self.store.get_peer(peer_id)

    def create_stream(self, stream_spec: dict[str, Any]) -> Stream:
        return self.store.create_stream(Stream.model_validate(stream_spec))

    def get_stream(self, stream_id: str) -> Stream | None:
        return self.store.get_stream(stream_id)

    def cas_stream(self, stream_id: str, expected_revision: int, mutation: Any) -> Stream:
        return self.store.cas_stream(stream_id, expected_revision, mutation)

    def append_record(self, append_request: dict[str, Any]) -> Record:
        return self.store.append_record(**append_request)

    def read_records(self, stream_id: str, after_position: int = 0, limit: int | None = None) -> list[Record]:
        return self.store.read_records(stream_id, after_position, 2**31 - 1 if limit is None else limit)

    def get_offset(self, peer_id: str, stream_id: str) -> Offset:
        return self.store.get_offset(peer_id, stream_id)

    def cas_offset(self, peer_id: str, stream_id: str, expected_revision: int, new_position: int) -> Offset:
        return self.store.advance_offset_cas(peer_id, stream_id, new_position, expected_revision)

    # --- strict wire boundary (schema-validated persistence)
    def persist_wire(self, kind: str, obj: Any) -> Any:
        from peerhub.core.wire import parse_wire  # lazy: wire layer is Wave 0 production code

        model = parse_wire(kind, obj)
        if kind == "peer":
            return self.store.register_peer(model)
        if kind == "stream":
            return self.store.create_stream(model)
        if kind == "record":
            from peerhub.core.wire import append_request_from_wire

            return self.store.append_record(**append_request_from_wire(model))
        if kind == "offset":  # wire revision = expected current revision; position = requested read-through
            return self.store.advance_offset_cas(model.peer_id, model.stream_id, model.read_through_position, model.revision)
        raise ValueError(f"no persistence port for {kind}")

    # --- observability of state
    def table_names(self) -> list[str]:
        with closing(sqlite3.connect(self.db_path)) as c:
            return sorted(r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))

    def row_counts(self) -> dict[str, int]:
        with closing(sqlite3.connect(self.db_path)) as c:
            return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in self.table_names()}

    def table_digests(self) -> dict[str, str]:
        out = {}
        with closing(sqlite3.connect(self.db_path)) as c:
            for t in self.table_names():
                h = hashlib.sha256()
                for row in c.execute(f"SELECT * FROM {t} ORDER BY 1,2"):
                    h.update(json.dumps(row, default=str).encode())
                out[t] = h.hexdigest()
        return out

    def state_digest(self) -> str:
        return compute_state_digest(self.db_path)
