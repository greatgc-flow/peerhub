"""Empirical parity and stamping proofs for the consolidated Alembic baseline."""

from __future__ import annotations

from pathlib import Path
import re
import sqlite3
from typing import cast

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest

from peerhub.persistence.sqlite import SqliteStateStore


PROJECT_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_REVISION = "v19_consolidated"


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _normalize_sql(sql: str | None) -> str | None:
    if sql is None:
        return None
    return re.sub(r"\s+", " ", sql.strip()).replace('"', "")


def _build_bespoke_database(path: Path) -> None:
    store = SqliteStateStore(
        path,
        workspace_home_id="alembic-baseline-parity-workspace",
    )
    store.initialize()
    store.close()


def _alembic_config() -> Config:
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def _domain_schema_inventory(path: Path) -> dict[str, object]:
    with sqlite3.connect(path) as connection:
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name <> 'alembic_version' "
                "ORDER BY name"
            )
        )
        objects = {
            (kind, name): _normalize_sql(sql)
            for kind, name, sql in connection.execute(
                "SELECT type, name, sql FROM sqlite_master "
                "WHERE sql IS NOT NULL "
                "AND name <> 'alembic_version' "
                "AND tbl_name <> 'alembic_version' "
                "ORDER BY type, name"
            )
        }
        columns = {
            table: tuple(
                connection.execute(
                    f"PRAGMA table_xinfo({_quote_identifier(table)})"
                )
            )
            for table in tables
        }
        foreign_keys = {
            table: tuple(
                connection.execute(
                    f"PRAGMA foreign_key_list({_quote_identifier(table)})"
                )
            )
            for table in tables
        }
        indexes: dict[str, tuple[tuple[object, ...], ...]] = {}
        index_columns: dict[str, tuple[tuple[object, ...], ...]] = {}
        for table in tables:
            table_indexes: list[tuple[object, ...]] = []
            for _, name, unique, origin, partial in connection.execute(
                f"PRAGMA index_list({_quote_identifier(table)})"
            ):
                table_indexes.append((name, unique, origin, partial))
                index_columns[name] = tuple(
                    connection.execute(
                        f"PRAGMA index_xinfo({_quote_identifier(name)})"
                    )
                )
            indexes[table] = tuple(sorted(table_indexes))

        return {
            "user_version": connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0],
            "tables": tables,
            "objects": objects,
            "columns": columns,
            "foreign_keys": foreign_keys,
            "indexes": indexes,
            "index_columns": index_columns,
            "schema_migrations": tuple(
                connection.execute(
                    "SELECT version, name FROM schema_migrations "
                    "ORDER BY version"
                )
            ),
            "foreign_key_check": tuple(
                connection.execute("PRAGMA foreign_key_check")
            ),
        }


def _alembic_revision(path: Path) -> tuple[str, ...]:
    with sqlite3.connect(path) as connection:
        return tuple(
            row[0]
            for row in connection.execute(
                "SELECT version_num FROM alembic_version"
            )
        )


def test_consolidated_alembic_baseline_matches_bespoke_v19(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bespoke_path = tmp_path / "bespoke.sqlite3"
    _build_bespoke_database(bespoke_path)

    alembic_workspace = tmp_path / "alembic-workspace"
    alembic_database_dir = alembic_workspace / ".peerhub"
    alembic_database_dir.mkdir(parents=True)
    alembic_path = alembic_database_dir / "peerhub.sqlite3"
    monkeypatch.chdir(alembic_workspace)

    config = _alembic_config()
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == [ALEMBIC_REVISION]
    assert tuple(revision.revision for revision in script.walk_revisions()) == (
        ALEMBIC_REVISION,
    )
    command.upgrade(config, "head")

    bespoke_inventory = _domain_schema_inventory(bespoke_path)
    alembic_inventory = _domain_schema_inventory(alembic_path)
    assert alembic_inventory == bespoke_inventory
    assert bespoke_inventory["user_version"] == 19
    tables = cast(tuple[str, ...], bespoke_inventory["tables"])
    objects = cast(
        dict[tuple[str, str], str | None],
        bespoke_inventory["objects"],
    )
    indexes = cast(
        dict[str, tuple[tuple[object, ...], ...]],
        bespoke_inventory["indexes"],
    )
    foreign_keys = cast(
        dict[str, tuple[tuple[object, ...], ...]],
        bespoke_inventory["foreign_keys"],
    )
    foreign_key_check = cast(
        tuple[tuple[object, ...], ...],
        bespoke_inventory["foreign_key_check"],
    )
    assert len(tables) == 39
    assert len(objects) == 48
    assert sum(
        len(table_indexes)
        for table_indexes in indexes.values()
    ) == 70
    assert sum(
        len(keys)
        for keys in foreign_keys.values()
    ) == 41
    assert foreign_key_check == ()
    assert _alembic_revision(alembic_path) == (ALEMBIC_REVISION,)


def test_stamp_marks_existing_bespoke_v19_without_replaying_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "existing-workspace"
    database_dir = workspace / ".peerhub"
    database_dir.mkdir(parents=True)
    database_path = database_dir / "peerhub.sqlite3"
    _build_bespoke_database(database_path)
    before = _domain_schema_inventory(database_path)

    monkeypatch.chdir(workspace)
    command.stamp(_alembic_config(), ALEMBIC_REVISION)

    assert _domain_schema_inventory(database_path) == before
    assert _alembic_revision(database_path) == (ALEMBIC_REVISION,)
