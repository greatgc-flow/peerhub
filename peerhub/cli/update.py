"""Read-only release checks and a manual upgrade plan."""
from __future__ import annotations

import argparse
import json
import os
import sys
import re
import shutil
import sqlite3
import subprocess
import time
import tomllib
from contextlib import closing
from datetime import UTC, datetime
from http.client import HTTPException
from importlib.resources import files
from pathlib import Path
from typing import Any, cast
from urllib.request import Request, urlopen

import psutil

from peerhub._console import format_shell_command
from peerhub._version import __version__
from peerhub.cli.support import error_line
from peerhub.core.connection import create_connection
from peerhub.core.schema_version import SUPPORTED_SCHEMA_VERSION, SchemaVersionError, future_schema_message
from peerhub.extensions.adapters.process import run_bounded
from peerhub.extensions.bridge_registry import eligible
from peerhub.extensions.caller import host_id
from peerhub.extensions.quota_probes import real_command
from peerhub.extensions.session_receipts import json_object

_RELEASES = {
    "peerhub": "https://pypi.org/pypi/peerhub/json",
    "codex": "https://api.github.com/repos/openai/codex/releases/latest",
    "claude": "https://api.github.com/repos/anthropics/claude-code/releases/latest",
    "ant": "https://api.github.com/repos/anthropics/anthropic-cli/releases/latest",
}
_VERSION = re.compile(r"(?<![0-9])[0-9]{1,12}\.[0-9]{1,12}\.[0-9]{1,12}(?:[-+][0-9A-Za-z.-]+)?\b")
_LEAD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_BATCH_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_METADATA_LIMIT = 2 * 1024 * 1024
UPDATE_BACKUP_SUFFIX = ".pre-update-{timestamp}.bak"
UPDATE_BACKUP_TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"
_PROVIDERS = {"codex": "cx", "claude": "cc", "agy": "ag", "ant": "ca"}


def _version(value: object) -> str | None:
    match = _VERSION.search(value) if isinstance(value, str) else None
    return match[0] if match else None


def _latest(tool: str) -> str | None:
    url = _RELEASES.get(tool)
    if url is None:
        return None
    try:
        request = Request(url, headers={"Accept": "application/json", "User-Agent": f"peerhub/{__version__}"})
        with urlopen(request, timeout=2) as response:
            raw = response.read(_METADATA_LIMIT + 1)
        data = json_object(raw.decode("utf-8")) if len(raw) <= _METADATA_LIMIT else {}
        info: object = data.get("info")
        return _version(cast(dict[str, object], info).get("version") if isinstance(info, dict) else data.get("tag_name"))
    except (OSError, HTTPException, ValueError, RecursionError):
        return None


def _local_command(tool: str) -> list[str]:
    try:
        if tool == "ant":
            path = shutil.which(tool)
            return [path] if path else []
        return real_command(_PROVIDERS[tool]) or []
    except (OSError, RuntimeError, ValueError):
        return []


def _local_output(command: list[str], *flags: str) -> str:
    if not command:
        return ""
    try:
        result = run_bounded([*command, *flags], stdin=None, cwd=os.getcwd(), env=os.environ,
                             timeout_s=3, max_bytes=_METADATA_LIMIT)
        if result.returncode == 0 and not (result.timed_out or result.output_truncated or result.drain_timed_out):
            return (result.stdout or result.stderr).decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _version_key(value: str) -> tuple[int, int, int, bool]:
    major, minor, patch = re.split(r"[-+]", value, maxsplit=1)[0].split(".")
    return int(major), int(minor), int(patch), "-" not in value.split("+", 1)[0]


def _check() -> dict[str, Any]:
    packaged = tomllib.loads(files("peerhub.config_data").joinpath("model-defaults.toml").read_text(encoding="utf-8"))
    observed = packaged["observed_versions"]
    commands = {tool: _local_command(tool) for tool in _PROVIDERS}
    installed = {"peerhub": __version__, **{tool: _version(_local_output(cmd, "--version")) for tool, cmd in commands.items()}}
    versions: dict[str, Any] = {}
    for tool, local in installed.items():
        latest, baseline = _latest(tool), observed.get(_PROVIDERS.get(tool))
        local_version = _version(local)
        versions[tool] = dict(installed=local, latest=latest, packaged=baseline,
                              update_available=_version_key(latest) > _version_key(local_version) if latest and local_version else None,
                              differs_from_packaged=local != baseline if local and baseline else None)

    # The bundled flag avoids a refresh of Codex's account-dependent catalog.
    raw = _local_output(commands["codex"], "debug", "models", "--bundled")
    try:
        catalog = json.JSONDecoder().raw_decode(raw.lstrip())[0]
        entries: object = cast(dict[str, object], catalog).get("models") if isinstance(catalog, dict) else None
    except (ValueError, RecursionError):
        entries = None
    models: set[str] = set()
    if isinstance(entries, list):
        for entry in cast(list[object], entries):
            slug = cast(dict[str, object], entry).get("slug") if isinstance(entry, dict) else None
            if isinstance(slug, str) and slug:
                models.add(slug)
    profiles: dict[str, dict[str, Any]] = packaged["profiles"]
    missing = {
        name: profile["model"] for name, profile in profiles.items()
        if models and name.startswith("cx.") and "model" in profile and profile["model"] not in models
    }
    return dict(status="OK", defaults_revision=packaged["defaults_revision"], versions=versions,
                codex_models=dict(source="bundled", status="OK" if models else "UNKNOWN", missing=missing))


def _process_name(path: str) -> str:
    name = Path(path).name.lower()
    return re.sub(r"(?:-script\.pyw?|\.exe|\.cmd|\.bat|\.js)$", "", name)


def _process_command(name: str, argv: list[str]) -> str | None:
    for index, arg in enumerate(argv[:5]):
        base = _process_name(arg)
        if base == "peerhub" or (index and argv[index - 1] == "-m" and arg in ("peerhub", "peerhub.cli")):
            rest = argv[index + 1:]
            while rest and (rest[0] in ("--db", "--json") or rest[0].startswith("--db=")):
                rest = rest[2 if rest[0] == "--db" else 1:]
            if rest and rest[0] in ("ask", "batch", "monitor", "diag"):
                return f"peerhub {rest[0]}"
            return None
        if base in _PROVIDERS and (index == 0 or "/" in arg or "\\" in arg):
            return base
        if index == 0 and not base.startswith(("python", "node")) and base not in ("py", "uv", "bash", "sh"):
            break
    base = _process_name(name)
    if not argv and base == "peerhub":
        return "peerhub (arguments unavailable)"
    return base if base in _PROVIDERS else None


def _blocking_processes() -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for proc in psutil.process_iter():
        try:
            name = proc.name()
            try:
                argv = proc.cmdline()
            except psutil.AccessDenied:
                argv = []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        command = _process_command(name, argv)
        if command:
            blockers.append(dict(pid=proc.pid, command=command))
    return sorted(blockers, key=lambda row: row["pid"])


def _shell(*argv: str) -> str:
    return format_shell_command(list(argv))


def _python(*argv: str) -> str:
    """Run the interpreter that runs this PeerHub (a pipx or uv tool venv has peerhub; a bare `python` may not)."""
    return ("& " if os.name == "nt" else "") + _shell(sys.executable, *argv)


def _caller_cwd(caller: dict[str, Any], host: str | None) -> str | None:
    cwd: object = caller.get("cwd")
    if host and caller.get("v") == 1 and caller.get("host") == host and isinstance(cwd, str) and cwd.isprintable():
        return cwd if Path(cwd).is_absolute() else None
    return None


def _has_columns(conn: sqlite3.Connection, table: str, *columns: str) -> bool:
    return set(columns) <= {row[0] for row in conn.execute("SELECT name FROM pragma_table_info(?)", (table,))}


def _caller(raw: str | None) -> dict[str, Any]:
    try:
        return json_object(raw)
    except RecursionError:
        return {}


_STREAM_SESSIONS = """
SELECT h.*, (SELECT e.detail FROM bridge_deliveries d JOIN bridge_evidence e ON e.delivery_id=d.delivery_id
             WHERE d.stream_id=h.stream_id AND d.peer_id=h.peer_id AND d.session_generation=h.session_generation
               AND d.external_session_id=h.external_session_id AND e.kind='caller' ORDER BY e.seq DESC LIMIT 1) AS caller
FROM bridge_sessions p JOIN bridge_session_history h ON h.session_handle=p.session_handle
WHERE h.vendor_session_id IS NOT NULL AND h.vendor_session_id <> '' AND h.runtime_kind IN ('cx','cc','ag')
  AND (? IS NULL OR h.workspace_generation=?)
ORDER BY h.last_seen DESC, h.stream_id, h.peer_id
"""


def _resume_hints(conn: sqlite3.Connection, db: Path, generation: str | None, now: float) -> list[dict[str, str]]:
    if not _has_columns(conn, "bridge_evidence", "kind", "detail"):
        return []
    host = host_id()
    cli = ("peerhub", "--db", str(db))
    streams: dict[str, str] = {}
    if (_has_columns(conn, "bridge_sessions", "session_handle")
            and _has_columns(conn, "bridge_session_history", "vendor_session_id", "context_watermark_version")
            and _has_columns(conn, "bridge_deliveries", "external_session_id", "session_generation")):
        for row in conn.execute(_STREAM_SESSIONS, (generation, generation)):
            stream, peer, handle = row["stream_id"], row["peer_id"], row["session_handle"]
            cwd = _caller_cwd(_caller(row["caller"]), host)
            if (stream not in streams and cwd and eligible(row, now)
                    and all(isinstance(s, str) and s.isprintable() for s in (stream, peer, handle))):
                ask = _shell(*cli, "ask", f"--stream={stream}", f"--session={handle}", "--", peer)
                streams[stream] = (f"From {cwd}, use {ask} with a new prompt and the original "
                                   "workspace, profile/model and write settings. Put options before --.")

    leads: dict[str, str] = {}
    batches: dict[str, str] = {}
    for (raw,) in conn.execute("SELECT detail FROM bridge_evidence WHERE kind='caller' ORDER BY seq DESC"):
        caller = _caller(raw)
        cwd = _caller_cwd(caller, host)
        if not cwd:
            continue
        session, batch = caller.get("session"), caller.get("batch")
        if caller.get("tool") == "cc" and isinstance(session, str) and _LEAD_ID.fullmatch(session):
            leads.setdefault(session, f"From {cwd}, use {_shell('claude', '--resume', session)} with the original account.")
        if isinstance(batch, str) and _BATCH_ID.fullmatch(batch):
            batches.setdefault(batch, f"From {cwd}, rerun {_shell(*cli, 'batch', 'run')} with the original manifest, "
                                      f"{_shell('--batch-id', batch)} and the original task bindings after resolving uncertain tasks.")
    return [dict(kind=kind, id=key, hint=hint)
            for kind, hints in (("stream", streams), ("lead", leads), ("batch", batches)) for key, hint in sorted(hints.items())]


def _store_plan(conn: sqlite3.Connection, db: Path, generation: str | None) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN")
    version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SUPPORTED_SCHEMA_VERSION:
        raise SchemaVersionError(future_schema_message(version, SUPPORTED_SCHEMA_VERSION))
    now = time.time()
    running: list[dict[str, str]] = []
    if _has_columns(conn, "bridge_claims", "stream_id"):
        # Any live claim can be between acquiring the lease and recording a delivery.
        clause = " AND workspace_generation=?" if generation is not None and _has_columns(conn, "bridge_claims", "workspace_generation") else ""
        params = (now, generation) if clause else (now,)
        running = [dict(stream=s, peer=p) for s, p in conn.execute(
            "SELECT stream_id,peer_id FROM bridge_claims WHERE expires_at>?" + clause + " ORDER BY stream_id,peer_id", params)]
    return running, [] if running else _resume_hints(conn, db, generation, now)


def _read_store_plan(db: Path, generation: str | None) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    wal, shm, journal = (db.with_name(db.name + suffix) for suffix in ("-wal", "-shm", "-journal"))
    if wal.is_file() and shm.is_file():
        # Use SQLite's normal read locks to see the committed WAL.
        with closing(create_connection(db, readonly=True, timeout_ms=2000)) as conn:
            return _store_plan(conn, db, generation)
    if any(sidecar.exists() for sidecar in (wal, shm, journal)):
        raise OSError("Store journal is unsettled. Wait for the store to settle and rerun the plan.")
    # Even mode=ro creates sidecars for an idle WAL store. Use an immutable
    # read for that case and discard it if a concurrent writer changed the files.
    before = db.stat()
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        snapshot = _store_plan(conn, db, generation)
    after = db.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns) or any(
        sidecar.exists() for sidecar in (wal, shm, journal)
    ):
        raise OSError("Store changed during the read. Rerun the plan.")
    return snapshot


def _upgrade_commands(db: Path) -> list[dict[str, Any]]:
    # SQLite's online backup includes committed WAL contents. Reserve a new destination.
    backup = ("import sqlite3,sys; from pathlib import Path; dest=Path(sys.argv[2]); "
              "dest.touch(exist_ok=False); sqlite3.connect(Path(sys.argv[1]).as_uri()+'?mode=ro',uri=True)"
              ".backup(sqlite3.connect(dest))")
    restore_snapshot = ("import sys; from pathlib import Path; from peerhub.core.workspace import Workspace; "
                        "db=Path(sys.argv[1]); Workspace(db.parent, db_name=db.name).restore_snapshot(Path(sys.argv[2]))")
    db_path = str(db)
    timestamp = datetime.fromtimestamp(time.time(), UTC).strftime(UPDATE_BACKUP_TIMESTAMP_FORMAT)
    backup_path = str(db.with_name(db.name + UPDATE_BACKUP_SUFFIX.format(timestamp=timestamp)))
    restore = _python("-c", restore_snapshot, db_path, backup_path)
    health = _shell("peerhub", "--db", db_path, "diag", "health")
    restore = f"{restore}; if ($?) {{ {health} }}" if os.name == "nt" else f"{restore} && {health}"
    return [
        dict(step="Copy the selected store. Continue after the copy succeeds.",
             command=_python("-c", backup, db_path, backup_path),
             restore=dict(step="To restore, stop all clients first.", command=restore)),
        dict(step="Upgrade PeerHub with the installer you originally used. Choose one.",
             command="pipx upgrade peerhub", alternatives=["uv tool upgrade peerhub", "python -m pip install -U peerhub"]),
        dict(step="Verify the upgraded CLI.", command="peerhub --version"),
        dict(step="Check the upgraded store.", command=_shell("peerhub", "--db", db_path, "diag", "health")),
        dict(step="Restart the monitor if you use it.", command=_shell("peerhub", "--db", db_path, "monitor")),
    ]


def _plan(db: Path) -> tuple[dict[str, Any], int]:
    processes = _blocking_processes()
    report: dict[str, Any] = dict(status="BLOCKED", db=str(db), running_asks=[], processes=processes, commands=[], resume=[])
    try:
        if not db.is_file():
            return dict(report, status="UNAVAILABLE", error="Selected store is missing. Select an existing store with --db."), 5
        marker = db.parent / "workspace.generation"
        generation = marker.read_text(encoding="utf-8") if marker.is_file() else None
        running, hints = _read_store_plan(db, generation)
    except SchemaVersionError as exc:
        return dict(report, status="UNAVAILABLE", error=str(exc)), 6
    except (OSError, sqlite3.Error, ValueError) as exc:
        return dict(report, status="UNAVAILABLE", error=str(exc)), 4
    report["running_asks"] = running
    if running or any(p["command"] in ("peerhub ask", "peerhub (arguments unavailable)") for p in processes):
        return dict(report, next="Wait for running asks to settle, then rerun the plan."), 1
    return dict(report, status="READY", commands=_upgrade_commands(db), resume=hints,
                next="Pause new asks and batches. Close listed blockers normally and rerun the plan just before upgrading."), 0


def run_update(args: argparse.Namespace) -> int:
    if args.update_check:
        report = _check()
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"Packaged defaults {report['defaults_revision']}")
            for tool, row in report["versions"].items():
                print(f"{tool}: installed {row['installed'] or 'unknown'}, latest {row['latest'] or 'unknown'}, "
                      f"packaged {row['packaged'] or '-'}"
                      + (" (update available)" if row["update_available"] else "")
                      + (" (differs from packaged)" if row["differs_from_packaged"] else ""))
            catalog = report["codex_models"]
            missing = ", ".join(f"{profile}={model}" for profile, model in catalog["missing"].items())
            print(f"Codex bundled models: {missing or catalog['status']}" + (" missing" if missing else ""))
        return 0

    report, code = _plan(Path(args.db))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Upgrade plan for {report['db']} ({report['status']})")
        for row in report["running_asks"]:
            print(f"Active ask lease: {row['stream']!r}, peer {row['peer']!r}")
        for proc in report["processes"]:
            print(f"Blocking process: PID {proc['pid']} {proc['command']}")
        if code == 0:
            print(report["next"])
        for num, step in enumerate(report["commands"], 1):
            print(f"{num}. {step['step']}\n   {step['command']}")
            if restore := step.get("restore"):
                print(f"   {restore['step']}\n   {restore['command']}")
            for alternative in step.get("alternatives", []):
                print(f"   or {alternative}")
        for row in report["resume"]:
            print(f"Resume {row['kind']} {row['id']}: {row['hint']}")
    if "error" in report:
        error_line({4: "storage_corrupt", 5: "store_missing", 6: "schema_version_error"}[code], report["error"])
    elif code == 1:
        error_line("ask_claim_held", "An active ask lease or a potentially running local ask blocks the plan.", report["next"])
    return code
