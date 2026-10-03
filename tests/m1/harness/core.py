"""Core harness adapter over peerhub.m1 (CoreStore). Bridge/Observation/Diag harness ports arrive with their waves."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from peerhub.m1.models import Offset, Peer, Record, Stream
from peerhub.m1.store import CoreStore


class CoreHarness:
    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.db_path = self.workspace / "core.db"
        gen = self.workspace / "workspace.generation"
        if not gen.exists():
            gen.write_text(uuid.uuid4().hex, encoding="utf-8")
        self.store = CoreStore(self.db_path)

    # --- identity / lifecycle
    def reopen(self) -> "CoreHarness":
        self.store = CoreStore(self.db_path)
        return self

    def workspace_generation(self) -> str:
        return (self.workspace / "workspace.generation").read_text(encoding="utf-8")

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
        raise NotImplementedError("Stream CAS is a Wave 1 (STR) capability")

    def append_record(self, append_request: dict[str, Any]) -> Record:
        return self.store.append_record(**append_request)

    def read_records(self, stream_id: str, after_position: int = 0, limit: int = 1000) -> list[Record]:
        return self.store.read_records(stream_id, after_position, limit)

    def get_offset(self, peer_id: str, stream_id: str) -> Offset:
        return self.store.get_offset(peer_id, stream_id)

    def cas_offset(self, peer_id: str, stream_id: str, expected_revision: int, new_position: int) -> Offset:
        return self.store.advance_offset_cas(peer_id, stream_id, new_position, expected_revision)

    # --- strict wire boundary (schema-validated persistence)
    def persist_wire(self, kind: str, obj: Any) -> Any:
        from peerhub.m1.wire import parse_wire  # lazy: wire layer is Wave 0 production code

        model = parse_wire(kind, obj)
        if kind == "peer":
            return self.store.register_peer(model)
        if kind == "stream":
            return self.store.create_stream(model)
        raise ValueError(f"no persistence port for {kind}")

    # --- observability of state
    def table_names(self) -> list[str]:
        with sqlite3.connect(self.db_path) as c:
            return sorted(r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))

    def state_digest(self) -> str:
        h = hashlib.sha256()
        with sqlite3.connect(self.db_path) as c:
            for t in self.table_names():
                h.update(t.encode())
                for row in c.execute(f"SELECT * FROM {t} ORDER BY 1,2"):
                    h.update(json.dumps(row, default=str).encode())
        return h.hexdigest()
