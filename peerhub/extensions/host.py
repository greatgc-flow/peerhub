"""M2.0 Generic Extension Host Implementation.

Adheres strictly to M2_0_EXTENSION_HOST_CONTRACT.md:
- SQLite WAL transaction isolation for extension schemas
- Strict prefix enforcement: all extension tables must begin with 'ext_' (or 'm2_')
- Complete state machine and lifecycle management: DISCOVERED, VALIDATED, ENABLED, DISABLED, FAILED, MIGRATING
- Dynamic loading and isolation, module eviction on disable
- Hook registration, safe exception trapping, and event dispatch
- Registry cache rebuilding and schema downgrade prevention
"""

from __future__ import annotations

import gc
import json
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any, Callable, ClassVar

from peerhub.extensions.sqlite_tx import sqlite_tx
from peerhub.extensions.manifest import ExtensionManifest, SchemaValidationError, parse_dependency, validate_manifest


class ExtensionError(Exception):
    """Base exception for Extension Host errors."""


class SchemaPrefixViolationError(ExtensionError, ValueError):
    """Extension attempted to create or alter an unprefixed database table."""


class ForbiddenTransitionError(ExtensionError, RuntimeError):
    """Attempting an invalid state machine transition."""


class MissingDependencyError(ExtensionError, RuntimeError):
    """Extension requires an unavailable or disabled dependency."""


class RegistrationConflictError(ExtensionError, ValueError):
    """Two extensions claim the same ID."""


class DowngradeNotSupportedError(ExtensionError, RuntimeError):
    """Schema version loaded is older than DB version."""


class MigrationCrashError(ExtensionError, RuntimeError):
    """Process forcefully died mid-migration."""


class ExtensionHookError(ExtensionError, RuntimeError):
    """User extension callback throws unhandled exception."""


CREATE_TABLE_PATTERN = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\"`\[]?([a-zA-Z0-9_]+)[\"`\]]?)",
    re.IGNORECASE,
)

ALTER_TABLE_PATTERN = re.compile(
    r"ALTER\s+TABLE\s+([\"`\[]?([a-zA-Z0-9_]+)[\"`\]]?)",
    re.IGNORECASE,
)


class ExtensionHost:
    """Core extension host managing discovery, lifecycle, and schema boundaries."""

    ALLOWED_TRANSITIONS: ClassVar[set[tuple[str, str]]] = {
        ("DISCOVERED", "VALIDATED"),
        ("DISCOVERED", "FAILED"),
        ("VALIDATED", "ENABLED"),
        ("VALIDATED", "FAILED"),
        ("ENABLED", "DISABLED"),
        ("DISABLED", "ENABLED"),
        ("ENABLED", "MIGRATING"),
        ("MIGRATING", "ENABLED"),
        ("MIGRATING", "FAILED"),
        ("ENABLED", "FAILED"),
    }

    def __init__(self, db_path: Path | str, extensions_dir: Path | str | None = None) -> None:
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.extensions_dir = Path(extensions_dir).resolve() if extensions_dir else None
        self.manifests: dict[str, ExtensionManifest] = {}
        self.manifest_dirs: dict[str, Path] = {}
        self.loaded_modules: dict[str, Any] = {}
        self.hooks: dict[str, list[tuple[str, Callable[[Any], None]]]] = {}
        self._init_db()

    def _init_db(self) -> None:
        with sqlite_tx(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("""
            CREATE TABLE IF NOT EXISTS m2_extension_registry (
                id TEXT PRIMARY KEY,
                version TEXT NOT NULL,
                entrypoint TEXT NOT NULL,
                state TEXT NOT NULL,
                schema_version INTEGER NOT NULL DEFAULT 1,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                installed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            """)

    def get_connection(self) -> sqlite3.Connection:
        """Create a database connection configured with foreign keys and busy timeout."""
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def register_manifest(
        self,
        manifest: ExtensionManifest,
        path: Path | str,
        target_schema_version: int | None = None,
    ) -> None:
        """Register an extension manifest into host memory index and database registry."""
        ext_dir = Path(path).resolve()
        ext_id = manifest.id
        entrypoint = Path(manifest.entrypoint)
        if not manifest.entrypoint or entrypoint.is_absolute() or ".." in entrypoint.parts:
            raise SchemaValidationError("Extension entrypoint must remain within its source directory")
        try:
            (ext_dir / entrypoint).resolve().relative_to(ext_dir)
        except ValueError as exc:
            raise SchemaValidationError("Extension entrypoint escapes its source directory") from exc

        if ext_id in self.manifests and self.manifests[ext_id] != manifest:
            raise RegistrationConflictError(
                f"Extension ID {ext_id!r} already registered with different manifest"
            )

        with sqlite_tx(self.db_path) as conn:
            row = conn.execute(
                "SELECT state, schema_version FROM m2_extension_registry WHERE id = ?",
                (ext_id,),
            ).fetchone()
            if row is not None:
                db_schema_version = row[1]
                declared_version = target_schema_version if target_schema_version is not None else 1
                if db_schema_version > declared_version:
                    raise DowngradeNotSupportedError(
                        f"Extension {ext_id!r} schema downgrade from DB version {db_schema_version} to {declared_version} is forbidden"
                    )
                registered = conn.execute("SELECT version FROM m2_extension_registry WHERE id = ?", (ext_id,)).fetchone()[0]
                if registered != manifest.version:  # a version change is never adopted silently (dependents pin registered versions)
                    raise RegistrationConflictError(
                        f"Extension {ext_id!r} is registered at version {registered}, manifest says {manifest.version}")
            else:
                initial_version = target_schema_version if target_schema_version is not None else 1
                conn.execute(
                    """
                    INSERT INTO m2_extension_registry (id, version, entrypoint, state, schema_version, metadata_json)
                    VALUES (?, ?, ?, 'DISCOVERED', ?, ?)
                    """,
                    (ext_id, manifest.version, manifest.entrypoint, initial_version, manifest.model_dump_json()),
                )

        self.manifests[ext_id] = manifest
        self.manifest_dirs[ext_id] = ext_dir

    def discover(self, path: Path | str) -> ExtensionManifest:
        """Scan a directory for manifest.json, validate it, and register it."""
        dir_path = Path(path).resolve()
        manifest_file = dir_path / "manifest.json"
        if not manifest_file.is_file():
            raise FileNotFoundError(f"Manifest not found in {dir_path}")

        try:
            raw_data = json.loads(manifest_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SchemaValidationError(f"Invalid JSON in manifest: {e}") from e

        manifest = validate_manifest(raw_data)
        self.register_manifest(manifest, dir_path)
        return manifest

    def rebuild_registry(self) -> None:
        """Clear in-memory caches, evict loaded modules from sys.modules, and garbage collect."""
        for ext_id in list(self.loaded_modules.keys()):
            if ext_id in sys.modules:
                del sys.modules[ext_id]
        self.loaded_modules.clear()
        self.manifests.clear()
        self.manifest_dirs.clear()
        self.hooks.clear()
        gc.collect()

    def get_state(self, ext_id: str) -> str:
        """Get the current lifecycle state of an extension from the database registry."""
        with sqlite_tx(self.db_path) as conn:
            row = conn.execute(
                "SELECT state FROM m2_extension_registry WHERE id = ?",
                (ext_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Extension {ext_id!r} not found in registry")
            return row[0]

    def transition(self, ext_id: str, target_state: str) -> str:
        """Transition an extension to a target state, enforcing the lifecycle state machine."""
        current_state = self.get_state(ext_id)
        if current_state == target_state:
            return current_state

        if (current_state, target_state) not in self.ALLOWED_TRANSITIONS:
            raise ForbiddenTransitionError(
                f"Forbidden transition from {current_state} to {target_state} for extension {ext_id!r}"
            )

        with sqlite_tx(self.db_path) as conn:
            conn.execute(
                "UPDATE m2_extension_registry SET state = ? WHERE id = ?",
                (target_state, ext_id),
            )
        return target_state

    def _registered_version(self, ext_id: str) -> str | None:
        with sqlite_tx(self.db_path) as conn:
            row = conn.execute("SELECT version FROM m2_extension_registry WHERE id = ?", (ext_id,)).fetchone()
        return None if row is None else str(row[0])

    def _check_dependencies(self, ext_id: str, manifest: ExtensionManifest) -> None:
        """Every dependency must be installed, ENABLED and (when pinned with `==`) at exactly that registered version."""
        for entry in manifest.dependencies:
            dep_id, pinned = parse_dependency(entry)
            try:
                dep_state = self.get_state(dep_id)
            except KeyError:
                raise MissingDependencyError(f"Required dependency {dep_id!r} for {ext_id!r} is not installed") from None
            if dep_state != "ENABLED":
                raise MissingDependencyError(f"Required dependency {dep_id!r} for {ext_id!r} is not enabled (state: {dep_state})")
            installed = self._registered_version(dep_id)
            if pinned is not None and installed != pinned:
                raise MissingDependencyError(
                    f"Dependency {dep_id!r} for {ext_id!r} requires version {pinned}, registered version is {installed}")

    def boot(self) -> dict[str, Any]:
        """Boot discovery: register every manifest found under `extensions_dir`, then re-verify ENABLED extensions.

        Failure isolation: an unreadable or invalid manifest is reported and skipped, it never aborts the other extensions.
        Fail closed: an ENABLED extension whose dependencies are no longer satisfied is moved to FAILED (never left enabled).
        """
        report: dict[str, Any] = {"discovered": [], "errors": {}, "failed": {}}
        if self.extensions_dir is not None and self.extensions_dir.is_dir():
            for child in sorted(p for p in self.extensions_dir.iterdir() if p.is_dir() and (p / "manifest.json").is_file()):
                try:
                    report["discovered"].append(self.discover(child).id)
                except (ExtensionError, SchemaValidationError, ValueError, OSError) as exc:
                    report["errors"][child.name] = f"{type(exc).__name__}: {exc}"
        # An ENABLED registry row whose manifest could not be (re)read has nothing left to verify: it must not stay enabled.
        with sqlite_tx(self.db_path) as conn:
            enabled = [row[0] for row in conn.execute("SELECT id FROM m2_extension_registry WHERE state = 'ENABLED' ORDER BY id")]
        for ext_id in enabled:
            if ext_id not in self.manifests:
                self.transition(ext_id, "FAILED")
                report["failed"][ext_id] = "manifest missing or unreadable at boot"
        # Failures cascade: repeat until stable, so a dependent evaluated BEFORE its failing dependency is caught as well.
        changed = True
        while changed:
            changed = False
            for ext_id in sorted(self.manifests):
                if self.get_state(ext_id) != "ENABLED":
                    continue
                manifest = self.manifests[ext_id]
                try:
                    self._check_dependencies(ext_id, manifest)
                    if ext_id in self.manifest_dirs and not (self.manifest_dirs[ext_id] / manifest.entrypoint).is_file():
                        raise MissingDependencyError(f"entrypoint {manifest.entrypoint!r} is missing")
                except MissingDependencyError as exc:
                    self.transition(ext_id, "FAILED")
                    report["failed"][ext_id] = str(exc)
                    changed = True
        return report

    def enable(self, ext_id: str) -> str:
        """Enable an extension, resolving dependencies and dynamically loading its module."""
        current_state = self.get_state(ext_id)
        if current_state == "ENABLED":
            return "ENABLED"

        if current_state == "FAILED":
            raise ForbiddenTransitionError(f"Cannot enable extension {ext_id!r} directly from FAILED state")

        manifest = self.manifests.get(ext_id)
        if manifest:
            try:
                self._check_dependencies(ext_id, manifest)
            except MissingDependencyError:
                self.transition(ext_id, "FAILED")
                raise

        if manifest and ext_id in self.manifest_dirs and not (self.manifest_dirs[ext_id] / manifest.entrypoint).is_file():
            # Never leave metadata ENABLED without a runtime module: a missing entrypoint is a failed enablement.
            self.transition(ext_id, "FAILED")
            raise ExtensionHookError(f"Extension {ext_id!r} entrypoint {manifest.entrypoint!r} is missing")

        if current_state == "DISCOVERED":
            self.transition(ext_id, "VALIDATED")
            self.transition(ext_id, "ENABLED")
        elif current_state in ("DISABLED", "VALIDATED"):
            self.transition(ext_id, "ENABLED")

        # Load entrypoint into sys.modules and self.loaded_modules
        if manifest and ext_id in self.manifest_dirs:
            ext_dir = self.manifest_dirs[ext_id]
            entrypoint_path = ext_dir / manifest.entrypoint
            if entrypoint_path.is_file():
                import importlib.util

                spec = importlib.util.spec_from_file_location(ext_id, entrypoint_path)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[ext_id] = module
                    try:
                        spec.loader.exec_module(module)
                    except Exception as exc:
                        sys.modules.pop(ext_id, None)
                        self.transition(ext_id, "FAILED")
                        raise ExtensionHookError("Extension entrypoint failed during enablement") from exc
                    self.loaded_modules[ext_id] = module

        return "ENABLED"

    def disable(self, ext_id: str) -> str:
        """Disable an extension, unloading module and unbinding hooks while strictly preserving tables."""
        current_state = self.get_state(ext_id)
        if current_state == "DISABLED":
            return "DISABLED"

        if current_state == "FAILED":
            raise ForbiddenTransitionError(f"Cannot disable extension {ext_id!r} from FAILED state")

        self.transition(ext_id, "DISABLED")

        if ext_id in self.loaded_modules:
            del self.loaded_modules[ext_id]
        if ext_id in sys.modules:
            del sys.modules[ext_id]
        for event_name in list(self.hooks):
            self.hooks[event_name] = [(owner, callback) for owner, callback in self.hooks[event_name] if owner != ext_id]
            if not self.hooks[event_name]:
                del self.hooks[event_name]

        return "DISABLED"

    def register_hook(self, ext_id: str, event_name: str, callback: Callable[[Any], None]) -> None:
        """Register an event callback hook for an extension."""
        self.hooks.setdefault(event_name, []).append((ext_id, callback))

    def dispatch_event(self, event_name: str, payload: Any) -> None:
        """Dispatch a Core event to all registered extension hooks.

        If a hook raises an unhandled exception, it is caught at the host boundary,
        and the extension transitions to FAILED without crashing the Core (EXT-013).
        """
        for ext_id, callback in list(self.hooks.get(event_name, [])):
            try:
                state = self.get_state(ext_id)
            except KeyError:
                continue

            if state != "ENABLED":
                continue

            try:
                callback(payload)
            except Exception:
                try:
                    self.transition(ext_id, "FAILED")
                except Exception:
                    pass

    def apply_extension_schema(
        self,
        ext_id: str,
        sql: str,
        new_schema_version: int | None = None,
    ) -> None:
        """Apply an extension schema migration under strict table prefix enforcement."""
        # 1. Scan and validate all table declarations in SQL
        matches = CREATE_TABLE_PATTERN.findall(sql)
        for _, raw_name in matches:
            clean_name = raw_name.strip("\"'`[]")
            if not (clean_name.startswith("ext_") or clean_name.startswith("m2_")):
                raise SchemaPrefixViolationError(
                    f"Extension {ext_id!r} attempted to create un-prefixed table {clean_name!r}; "
                    f"all extension tables must start with 'ext_'"
                )

        alter_matches = ALTER_TABLE_PATTERN.findall(sql)
        for _, raw_name in alter_matches:
            clean_name = raw_name.strip("\"'`[]")
            if not (clean_name.startswith("ext_") or clean_name.startswith("m2_")):
                raise SchemaPrefixViolationError(
                    f"Extension {ext_id!r} attempted to alter un-prefixed table {clean_name!r}; "
                    f"all extension tables must start with 'ext_'"
                )

        # 2. Execute migration inside transaction
        with sqlite_tx(self.db_path, isolation_level=None) as conn:
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("BEGIN IMMEDIATE")
            violation: list[str] = []
            def authorize(action: int, arg1: str | None, arg2: str | None,
                          _db: str | None, _source: str | None) -> int:
                forbidden = {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA,
                             sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT}
                table_actions = {sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE,
                                 sqlite3.SQLITE_CREATE_TEMP_TABLE, sqlite3.SQLITE_DROP_TEMP_TABLE,
                                 sqlite3.SQLITE_CREATE_VIEW, sqlite3.SQLITE_DROP_VIEW,
                                 sqlite3.SQLITE_CREATE_TEMP_VIEW, sqlite3.SQLITE_DROP_TEMP_VIEW,
                                 sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
                target = arg2 if action in {sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_CREATE_INDEX,
                                           sqlite3.SQLITE_DROP_INDEX, sqlite3.SQLITE_CREATE_TRIGGER,
                                           sqlite3.SQLITE_DROP_TRIGGER} else arg1
                scoped = action in table_actions or action in {
                    sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_DROP_INDEX,
                    sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_DROP_TRIGGER}
                bad_table = (scoped and target not in {"sqlite_master", "sqlite_temp_master"}
                             and (target == "m2_extension_registry" or not target
                                  or not target.startswith(("ext_", "m2_"))))
                if (action in forbidden or bad_table or action in {sqlite3.SQLITE_CREATE_VTABLE, sqlite3.SQLITE_DROP_VTABLE}
                        or (action == sqlite3.SQLITE_FUNCTION and arg2 == "load_extension")):
                    violation.append("Migration attempted to access storage outside its extension boundary")
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            if new_schema_version is not None:
                row = conn.execute("SELECT schema_version FROM m2_extension_registry WHERE id=?", (ext_id,)).fetchone()
                if type(new_schema_version) is not int or new_schema_version < 1:
                    conn.rollback()
                    raise ValueError("schema version must be a positive integer")
                if row is not None and row[0] > new_schema_version:
                    conn.rollback()
                    raise DowngradeNotSupportedError("Schema downgrade is forbidden")
            before_tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            try:
                conn.set_authorizer(authorize)
                pending = ""
                for char in sql:
                    pending += char
                    if char == ";" and sqlite3.complete_statement(pending):
                        conn.execute(pending)
                        pending = ""
                if pending.strip():
                    conn.execute(pending)
                conn.set_authorizer(None)
                after_tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if any(not name.startswith(("ext_", "m2_")) for name in after_tables - before_tables):
                    raise SchemaPrefixViolationError("Migration created an unprefixed table")
                if new_schema_version is not None:
                    conn.execute("UPDATE m2_extension_registry SET schema_version=? WHERE id=?",
                                 (new_schema_version, ext_id))
                conn.commit()
            except Exception as exc:
                conn.set_authorizer(None)
                try:
                    conn.execute("ROLLBACK;")
                except Exception:
                    pass
                if violation:
                    raise SchemaPrefixViolationError(violation[0]) from exc
                raise
