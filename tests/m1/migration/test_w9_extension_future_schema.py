"""W9 / MIG-003: every extension store refuses a future Core schema BEFORE any DDL/DML (no dropped foreign trigger, no new tables)."""
import hashlib
import sqlite3
from contextlib import closing

import pytest

from peerhub.extensions.bridge import Bridge
from peerhub.extensions.bridge_claims import ClaimStore
from peerhub.extensions.observation import ObservationStore
from peerhub.extensions.session_bridge import SessionBridgeStore
from peerhub.m1.schema_version import SchemaVersionError
from peerhub.m1.store import CoreStore

pytestmark = [pytest.mark.migration, pytest.mark.cutover]


def _tree(d):
    return sorted((p.name, hashlib.sha256(p.read_bytes()).hexdigest()) for p in d.iterdir())


def _stamp(db, table, version=999):
    with closing(sqlite3.connect(db)) as c:
        c.execute(f"CREATE TRIGGER foreign_owned_trigger BEFORE DELETE ON {table} BEGIN SELECT 1; END")
        c.execute(f"PRAGMA user_version = {version}")
        c.commit()
        c.execute("PRAGMA journal_mode = DELETE")


def _make(kind, db):
    core = CoreStore(db)
    claims = ClaimStore(db, lambda: "g1")
    if kind == "observation":
        ObservationStore(db)
        return "observations", lambda: ObservationStore(db)
    if kind == "claims":
        return "bridge_finalizations", lambda: ClaimStore(db, lambda: "g1")
    Bridge(core, claims)
    return "bridge_deliveries", lambda: Bridge(core, claims)


@pytest.mark.parametrize("kind", ["observation", "claims", "bridge"])
def test_extension_store_refuses_future_schema_without_any_write(tmp_path, kind):
    db = tmp_path / "x" / "core.db"
    db.parent.mkdir()
    table, opener = _make(kind, db)
    with closing(sqlite3.connect(db)) as c:  # positive control: the unstamped store reopens fine and owns triggers
        assert c.execute("PRAGMA user_version").fetchone()[0] == 3
    opener()
    _stamp(db, table)
    before = _tree(db.parent)
    with pytest.raises(SchemaVersionError, match=r"999.*newer than supported 3.*nothing was changed"):
        opener()
    assert _tree(db.parent) == before
    with closing(sqlite3.connect(db)) as c:
        assert c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='foreign_owned_trigger'").fetchone()[0] == 1


@pytest.mark.parametrize("opener", [lambda p: ObservationStore(p), lambda p: ClaimStore(p, lambda: "g"), lambda p: SessionBridgeStore(p)])
def test_bare_future_database_gets_no_tables(tmp_path, opener):
    db = tmp_path / "future.db"
    with closing(sqlite3.connect(db)) as c:
        c.execute("CREATE TABLE keepme (a)")
        c.execute("PRAGMA user_version = 999")
        c.commit()
        c.execute("PRAGMA journal_mode = DELETE")
    before = _tree(tmp_path)
    with pytest.raises(SchemaVersionError):
        opener(db)
    assert _tree(tmp_path) == before
