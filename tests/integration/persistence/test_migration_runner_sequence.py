"""Sequence-completeness proofs for the bespoke SQLite migration runner.

The runner is atomic per migration and resumable across a sequence, but it
must never return normally from a state that only *looks* fully migrated.
These tests pin the three silent-desync modes: a migration file the runner
never applies, a migration that applies without recording itself, and a
database recording migrations this build does not ship.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
import shutil
import sqlite3

import pytest

from peerhub.persistence.sqlite import SqliteStateStore


REAL_MIGRATIONS = Path(str(resources.files("peerhub.persistence.migrations")))
LATEST_PACKAGED_VERSION = 20
NEXT_PACKAGED_VERSION = LATEST_PACKAGED_VERSION + 1


def _store(path: Path) -> SqliteStateStore:
    return SqliteStateStore(
        path,
        workspace_home_id="migration-sequence-workspace",
    )


def _database_path(tmp_path: Path) -> Path:
    return tmp_path / "workspace" / ".peerhub" / "peerhub.sqlite3"


def _migration_dir(
    tmp_path: Path,
    *,
    through: int = LATEST_PACKAGED_VERSION,
    extra: dict[str, str] | None = None,
    replace: dict[int, str] | None = None,
    name: str = "migrations",
) -> Path:
    """Build a migrations directory from the real scripts, plus overrides."""

    directory = tmp_path / name
    directory.mkdir()
    for entry in sorted(REAL_MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        version = int(entry.name[:4])
        if version > through:
            continue
        if replace is not None and version in replace:
            (directory / entry.name).write_text(
                replace[version],
                encoding="utf-8",
            )
        else:
            shutil.copy2(entry, directory / entry.name)
    for filename, text in (extra or {}).items():
        (directory / filename).write_text(text, encoding="utf-8")
    return directory


def _use_migrations(
    monkeypatch: pytest.MonkeyPatch,
    directory: Path,
) -> None:
    monkeypatch.setattr(
        SqliteStateStore,
        "_migration_directory",
        staticmethod(lambda: directory),
        raising=False,
    )


def _applied(path: Path) -> tuple[int, ...]:
    with sqlite3.connect(path) as connection:
        return tuple(
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        )


def _user_version(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


def _table_exists(path: Path, table: str) -> bool:
    with sqlite3.connect(path) as connection:
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            is not None
        )


_WELL_FORMED_NEXT = f"""
BEGIN IMMEDIATE;

CREATE TABLE sequence_probe (
    id TEXT PRIMARY KEY
);

INSERT INTO schema_migrations(version, name)
VALUES ({NEXT_PACKAGED_VERSION}, '{NEXT_PACKAGED_VERSION:04d}_sequence_probe');

PRAGMA user_version = {NEXT_PACKAGED_VERSION};

COMMIT;
"""

_UNRECORDED_NEXT = """
BEGIN IMMEDIATE;

CREATE TABLE sequence_probe (
    id TEXT PRIMARY KEY
);

COMMIT;
"""


def test_fresh_database_reaches_latest_packaged_migration(
    tmp_path: Path,
) -> None:
    """The real packaged sequence still lands on the authoritative schema."""

    database_path = _database_path(tmp_path)
    store = _store(database_path)
    store.initialize()
    store.close()

    assert _applied(database_path) == tuple(
        range(1, LATEST_PACKAGED_VERSION + 1)
    )
    assert _user_version(database_path) == LATEST_PACKAGED_VERSION


def test_migration_file_on_disk_is_applied_without_code_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new migration script is applied purely by existing on disk.

    Before the table-driven runner this required a hand-written guard in
    ``initialize()``; forgetting it left the database silently short of the
    available schema while ``initialize()`` returned normally.
    """

    _use_migrations(
        monkeypatch,
        _migration_dir(
            tmp_path,
            extra={
                f"{NEXT_PACKAGED_VERSION:04d}_sequence_probe.sql": (
                    _WELL_FORMED_NEXT
                )
            },
        ),
    )
    database_path = _database_path(tmp_path)

    store = _store(database_path)
    store.initialize()
    store.close()

    assert _applied(database_path) == tuple(
        range(1, NEXT_PACKAGED_VERSION + 1)
    )
    assert _user_version(database_path) == NEXT_PACKAGED_VERSION
    assert _table_exists(database_path, "sequence_probe")


def test_existing_database_picks_up_a_newly_added_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An already-initialized database advances when a script is added."""

    database_path = _database_path(tmp_path)
    _use_migrations(monkeypatch, _migration_dir(tmp_path, name="before"))
    store = _store(database_path)
    store.initialize()
    store.close()
    assert _user_version(database_path) == LATEST_PACKAGED_VERSION

    _use_migrations(
        monkeypatch,
        _migration_dir(
            tmp_path,
            extra={
                f"{NEXT_PACKAGED_VERSION:04d}_sequence_probe.sql": (
                    _WELL_FORMED_NEXT
                )
            },
            name="after",
        ),
    )
    store = _store(database_path)
    store.initialize()
    store.close()

    assert _applied(database_path) == tuple(
        range(1, NEXT_PACKAGED_VERSION + 1)
    )
    assert _table_exists(database_path, "sequence_probe")


def test_migration_that_does_not_record_itself_fails_loudly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A script that forgets its ``schema_migrations`` row is rejected.

    Without the completion check this migration would re-run on every single
    startup, forever, with no error.
    """

    _use_migrations(
        monkeypatch,
        _migration_dir(
            tmp_path,
            extra={
                f"{NEXT_PACKAGED_VERSION:04d}_sequence_probe.sql": (
                    _UNRECORDED_NEXT
                )
            },
        ),
    )
    database_path = _database_path(tmp_path)

    store = _store(database_path)
    with pytest.raises(RuntimeError) as failure:
        store.initialize()

    message = str(failure.value)
    assert str(NEXT_PACKAGED_VERSION) in message
    assert "schema_migrations" in message


def test_database_recording_unknown_migrations_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A database written by a newer build must not be silently accepted."""

    _use_migrations(monkeypatch, _migration_dir(tmp_path))
    database_path = _database_path(tmp_path)
    store = _store(database_path)
    store.initialize()
    store.close()

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO schema_migrations(version, name) "
            "VALUES (?, ?)",
            (
                NEXT_PACKAGED_VERSION,
                f"{NEXT_PACKAGED_VERSION:04d}_from_a_newer_build",
            ),
        )
        connection.commit()

    with pytest.raises(RuntimeError) as failure:
        _store(database_path).initialize()

    assert str(NEXT_PACKAGED_VERSION) in str(failure.value)


def test_interrupted_sequence_raises_then_resumes_on_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mid-sequence failure is loud, and the next run finishes the job."""

    broken = _migration_dir(
        tmp_path,
        replace={14: "THIS IS NOT VALID SQL;\n"},
        name="broken",
    )
    _use_migrations(monkeypatch, broken)
    database_path = _database_path(tmp_path)

    with pytest.raises(sqlite3.OperationalError):
        _store(database_path).initialize()

    assert _applied(database_path) == tuple(range(1, 14))
    assert _user_version(database_path) == 13

    _use_migrations(monkeypatch, _migration_dir(tmp_path, name="repaired"))
    store = _store(database_path)
    store.initialize()
    store.close()

    assert _applied(database_path) == tuple(
        range(1, LATEST_PACKAGED_VERSION + 1)
    )
    assert _user_version(database_path) == LATEST_PACKAGED_VERSION


def test_legacy_unwrapped_migration_leaves_no_partial_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migrations 0002/0009/0010 carry no transaction of their own.

    The runner must supply one, so a failure partway through such a script
    cannot commit half of it.
    """

    partial_0009 = (
        "CREATE TABLE legacy_partial_probe (id TEXT PRIMARY KEY);\n"
        "THIS IS NOT VALID SQL;\n"
    )
    _use_migrations(
        monkeypatch,
        _migration_dir(tmp_path, through=9, replace={9: partial_0009}),
    )
    database_path = _database_path(tmp_path)

    with pytest.raises(sqlite3.OperationalError):
        _store(database_path).initialize()

    assert not _table_exists(database_path, "legacy_partial_probe")
    assert 9 not in _applied(database_path)


def test_duplicate_migration_version_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two scripts claiming the same version is an unresolvable ordering."""

    _use_migrations(
        monkeypatch,
        _migration_dir(
            tmp_path,
            extra={
                f"{NEXT_PACKAGED_VERSION:04d}_sequence_probe.sql": (
                    _WELL_FORMED_NEXT
                ),
                f"{NEXT_PACKAGED_VERSION:04d}_duplicate_probe.sql": (
                    _WELL_FORMED_NEXT
                ),
            },
        ),
    )

    with pytest.raises(RuntimeError) as failure:
        _store(_database_path(tmp_path)).initialize()

    assert "duplicate" in str(failure.value).lower()


def test_noncontiguous_migration_versions_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gap means a script is missing; guessing across it is unsafe."""

    _use_migrations(
        monkeypatch,
        _migration_dir(
            tmp_path,
            extra={
                f"{NEXT_PACKAGED_VERSION + 1:04d}_sequence_probe.sql": (
                    _WELL_FORMED_NEXT
                )
            },
        ),
    )

    with pytest.raises(RuntimeError) as failure:
        _store(_database_path(tmp_path)).initialize()

    assert str(NEXT_PACKAGED_VERSION) in str(failure.value)
