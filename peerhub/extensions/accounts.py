"""Immutable, single-user account roots; credentials are never opened or inspected."""
from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from peerhub.core.models import Peer
from peerhub.core.schema_version import SUPPORTED_SCHEMA_VERSION, SchemaVersionError, future_schema_message
from peerhub.core.store import CoreStore, storage_errors
from peerhub.extensions.peer_kinds import ALIASES

ROOT_VARS = {"cc": "CLAUDE_CONFIG_DIR", "cx": "CODEX_HOME"}
SCRUB_PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_", "OPENAI_", "CODEX_")
DDL = """CREATE TABLE IF NOT EXISTS account_bindings (
    peer_id TEXT NOT NULL PRIMARY KEY REFERENCES peers(peer_id),
    provider TEXT NOT NULL CHECK(provider IN ('cc','cx')),
    alias TEXT NOT NULL, slot_id TEXT NOT NULL UNIQUE,
    config_root TEXT NOT NULL UNIQUE COLLATE NOCASE, created_at TEXT NOT NULL)"""


class AccountError(ValueError):
    code = "account_invalid"


@dataclass(frozen=True)
class AccountRef:
    slot_id: str


@dataclass(frozen=True)
class AccountSnapshot:
    slot_id: str
    provider: str
    root_realpath: Path


@dataclass(frozen=True)
class AccountBinding:
    peer_id: str
    provider: str
    alias: str
    slot_id: str
    config_root: str
    created_at: str


def _version(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SUPPORTED_SCHEMA_VERSION:
        raise SchemaVersionError(future_schema_message(version, SUPPORTED_SCHEMA_VERSION))


def _valid_identity(provider: object, slot: object, root: object) -> bool:
    return (isinstance(provider, str) and provider in ROOT_VARS and isinstance(slot, str)
            and re.fullmatch(r"[0-9a-f]{16}", slot) is not None
            and isinstance(root, str) and Path(root).is_absolute())


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _rows(conn: sqlite3.Connection) -> list[AccountBinding]:
    _version(conn)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='account_bindings'").fetchone():
        return []
    rows = [AccountBinding(*row) for row in conn.execute(
        "SELECT peer_id,provider,alias,slot_id,config_root,created_at FROM account_bindings ORDER BY peer_id")]
    for binding in rows:
        if not _valid_identity(binding.provider, binding.slot_id, binding.config_root):
            raise AccountError("invalid account binding identity")
    return rows


def list_bindings(db: str | Path) -> list[AccountBinding]:
    path = Path(db).absolute()
    if not path.is_file():
        return []
    with storage_errors(), closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        return _rows(conn)


def get_binding(db: str | Path, peer_id: str) -> AccountBinding | None:
    return next((row for row in list_bindings(db) if row.peer_id == peer_id), None)


def _key(root: str | Path) -> str:
    return os.path.normpath(os.path.realpath(root)).casefold()


def _overlaps(a: str, b: str) -> bool:
    return a == b or a.startswith(b.rstrip("\\/") + os.sep) or b.startswith(a.rstrip("\\/") + os.sep)


def _validate_root(root: str | Path, others: list[AccountBinding], *, workspace: str | Path | None = None) -> Path:
    path = Path(root)
    if not path.is_absolute() or not path.is_dir():
        raise AccountError("account root must be an absolute existing directory")
    normalized = os.path.normcase(os.path.abspath(path))
    real = os.path.normcase(os.path.realpath(path))
    if normalized != real:
        raise AccountError("account root has a symlink/junction/reparse point in its realpath")
    protected: list[str | Path] = [row.config_root for row in others]
    protected.extend([Path.home() / ".claude", Path.home() / ".codex"])
    protected.extend(value for name in ROOT_VARS.values() if (value := os.environ.get(name)))
    if workspace is not None:
        protected.append(workspace)
    key = _key(path)
    if any(_overlaps(key, _key(other)) for other in protected):
        raise AccountError("account root overlaps another bound, ambient, default or workspace root")
    return Path(os.path.abspath(path))


def bind_account(db: str | Path, *, peer_id: str, provider: str, alias: str, root: str | Path) -> AccountBinding:
    if provider not in ROOT_VARS:
        why = "ag authentication lives in the OS keyring; account isolation is unsupported" if provider == "ag" else "only cc and cx are supported"
        raise AccountError(f"unsupported provider {provider!r}: {why}")
    peer: Peer = Peer.model_validate(dict(peer_id=peer_id, adapter_ref=provider, metadata={"account_bound": True}))
    if peer.peer_id in ALIASES:
        raise AccountError("builtin aliases are reserved; bind a new peer identity")
    if not _nonempty(alias):
        raise AccountError("account alias must be nonempty text")
    CoreStore(db)  # bootstrap Core schema; extension DDL never changes user_version
    with storage_errors(), closing(sqlite3.connect(db, isolation_level=None)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        _version(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(DDL)
            if conn.execute("SELECT 1 FROM peers WHERE peer_id=?", (peer.peer_id,)).fetchone():
                raise AccountError("peer already exists; bind a new peer identity")
            validated = _validate_root(root, _rows(conn))
            binding = AccountBinding(peer.peer_id, provider, alias, secrets.token_hex(8), str(validated), peer.created_at)
            conn.execute("INSERT INTO peers (peer_id,display_name,adapter_ref,metadata_json,created_at) VALUES (?,?,?,?,?)",
                         (peer.peer_id, peer.display_name, provider, json.dumps(peer.metadata), peer.created_at))
            conn.execute("INSERT INTO account_bindings (peer_id,provider,alias,slot_id,config_root,created_at) VALUES (?,?,?,?,?,?)",
                         (binding.peer_id, provider, alias, binding.slot_id, binding.config_root, binding.created_at))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return binding


def validate_launch(db: str | Path, binding: AccountBinding, workspace: str | Path) -> AccountSnapshot:
    current = get_binding(db, binding.peer_id)
    if current != binding:
        raise AccountError("account binding changed after claim acquisition")
    if binding.provider not in ROOT_VARS or re.fullmatch(r"[0-9a-f]{16}", binding.slot_id) is None:
        raise AccountError("invalid account binding identity")
    # Re-read the marker too: a peer registration must never silently unbind it.
    peer = CoreStore(db, readonly=True).get_peer(binding.peer_id)
    if peer is None or peer.metadata.get("account_bound") is not True or peer.adapter_ref != binding.provider:
        raise AccountError("account marker and binding disagree")
    root = _validate_root(binding.config_root, [b for b in list_bindings(db) if b.peer_id != binding.peer_id], workspace=workspace)
    return AccountSnapshot(binding.slot_id, binding.provider, root)


def scrub_names(env: Mapping[str, str], provider: str) -> list[str]:
    root_var = ROOT_VARS[provider]
    return [name for name in env if name.upper().startswith(SCRUB_PREFIXES) and name.upper() != root_var]
