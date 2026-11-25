"""Antigravity stdin transport discovery and caching."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, cast

from peerhub.core.load_limits import probe_timeout_factor
from peerhub.extensions.adapters import common
from peerhub.extensions.adapters.process import DEFAULT_MAX_BYTES, ProcessResult, SpawnFailure, decode_stderr

AG_TRANSPORT_ENV = "PEERHUB_AG_TRANSPORT"
_AG_PROBE_TIMEOUT_S = 10.0
_FileIdentity = tuple[str, int, int, int, int, int]
_TransportIdentity = tuple[_FileIdentity, ...]
_AG_TRANSPORT_CACHE: dict[tuple[_TransportIdentity, str], str] = {}
_AG_TRANSPORT_LOCK = threading.Lock()


@dataclass(frozen=True)
class AgTransport:
    transport: str  # stream | argv | undetermined | invalid_config
    source: str  # env | help
    version: str | None
    error: str | None = None

    def metadata(self) -> dict[str, Any]:
        return {"ag_transport": self.transport, "ag_transport_source": self.source, "ag_cli_version": self.version}


def _probe_failure(probe: str, r: ProcessResult) -> str | None:
    # output_truncated covers stderr too: the help text is read from both streams.
    for flag in ("timed_out", "silence_timed_out", "aborted", "stdout_truncated", "output_truncated", "drain_timed_out"):
        if getattr(r, flag):
            return f"{probe} {flag}"
    return None if r.returncode == 0 else f"{probe} exit {r.returncode}"


def _transport_cache_path(identity: _TransportIdentity, env: Mapping[str, str]) -> Path:
    configured = env.get("PEERHUB_CONFIG_HOME", "").strip()
    root = Path(configured).expanduser() if configured else Path.home() / ".peerhub" / "cache"
    digest = hashlib.sha256(json.dumps(identity).encode("ascii")).hexdigest()
    return root / "ag-transport-v2" / f"{digest}.json"


def _fingerprint(path: Path) -> _FileIdentity:
    real = os.path.normcase(str(path.resolve(strict=True)))
    st = os.stat(real)
    return real, st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


_LAUNCHER_EXE = re.compile(r'^[ \t]*@?(?:call[ \t]+)?(?:"([^"\r\n]+\.exe)"|([^\s"]+\.exe))[ \t]+%\*[ \t]*\r?$',
                           re.IGNORECASE | re.MULTILINE)
_LAUNCHER_ENV = re.compile(r"%([^%]+)%")


def _transport_identity(base: list[str], env: Mapping[str, str]) -> _TransportIdentity | None:
    if len(base) != 1:
        return None  # a multi-argument command can run code independent of base[0]
    launcher = Path(base[0])
    identity = (_fingerprint(launcher),)
    if launcher.suffix.lower() not in (".cmd", ".bat"):
        return identity
    with launcher.open("rb") as script:
        content = script.read(8193)
    if len(content) > 8192:
        return None
    source = content.decode("utf-8-sig", errors="replace")
    variables = {name.casefold(): value for name, value in env.items()}
    candidates: set[Path] = set()
    for match in _LAUNCHER_EXE.finditer(source):
        raw = re.sub(r"%~dp0|%dp0%", lambda _: str(launcher.absolute().parent) + os.sep,
                     match[1] or match[2], flags=re.IGNORECASE)
        raw = _LAUNCHER_ENV.sub(lambda m: variables.get(m[1].casefold(), m[0]), raw)
        exe = Path(raw)
        if "%" not in raw and exe.is_absolute() and exe.is_file():
            candidates.add(exe.resolve())
    if len(candidates) == 1:
        return (*identity, _fingerprint(candidates.pop()))
    return None  # unrecognized or ambiguous launchers are always probed


def _read_transport_cache(path: Path) -> AgTransport | None:
    try:
        with path.open(encoding="utf-8") as stream:
            content: object = json.loads(stream.read(512))
    except (OSError, ValueError):
        return None
    if isinstance(content, dict):
        data = cast(dict[str, object], content)
        version, transport = data.get("version"), data.get("transport")
        if isinstance(version, str) and common.VERSION_RE.fullmatch(version) and transport in ("stream", "argv"):
            return AgTransport(cast(str, transport), "help", version)
    return None


def _write_transport_cache(path: Path, version: str, transport: str) -> None:
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"version": version, "transport": transport}, stream)
        temporary.replace(path)
    except OSError:
        pass  # A cache is optional even on read-only hosts.
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _detect_ag_transport(base: list[str], env: Mapping[str, str], cwd: str, *,
                         renew: Callable[[], None] | None = None) -> AgTransport:
    """`stream` when `agy --help` documents stream-json input, `argv` when it lacks --input-format; else undetermined."""
    def undetermined(why: str, version: str | None = None) -> AgTransport:
        return AgTransport("undetermined", "help", version, f"cannot determine agy stdin support: {why}")

    try:
        identity = _transport_identity(base, env)
        cache_path = _transport_cache_path(identity, env) if identity is not None else None
    except (OSError, RuntimeError):
        identity, cache_path = None, None
    factor = probe_timeout_factor()
    retry = True

    def probe(arg: str) -> ProcessResult | str:
        nonlocal factor, retry
        while True:
            if renew is not None:
                renew()
            try:
                r = common.probe_cli(base, [arg], env=env, cwd=cwd, timeout_s=_AG_PROBE_TIMEOUT_S * factor,
                                     max_bytes=DEFAULT_MAX_BYTES)
            except SpawnFailure as e:
                return f"{arg} could not start ({e})"
            if not retry or not (r.timed_out or r.silence_timed_out or r.drain_timed_out) or r.aborted:
                return _probe_failure(arg, r) or r
            retry = False
            factor = max(factor, probe_timeout_factor())

    v = probe("--version")
    if isinstance(v, str):
        return undetermined(v)
    first = decode_stderr(v.stdout or v.stderr).strip().splitlines()[:1]
    m = common.VERSION_RE.search(first[0]) if first else None
    if m is None:
        return undetermined("--version printed no version")
    version = m.group(0)
    key = (identity, version) if identity is not None else None
    if key is not None and len(base) == 1:  # multi-argument wrappers probe their own help
        with _AG_TRANSPORT_LOCK:
            cached = _AG_TRANSPORT_CACHE.get(key)
        if cached is not None:
            return AgTransport(cached, "help", version)
    if cache_path is not None and factor > 1:
        disk = _read_transport_cache(cache_path)
        if disk is not None and disk.version == version:
            return disk
    h = probe("--help")
    if isinstance(h, str):
        return undetermined(h, version)
    text = (h.stdout + h.stderr).decode("utf-8", "replace")
    if "--input-format" not in text:
        transport = "argv"
    elif "stream-json" in text:
        transport = "stream"
    else:
        return undetermined("--help lists --input-format without stream-json", version)
    if key is not None:
        with _AG_TRANSPORT_LOCK:
            _AG_TRANSPORT_CACHE[key] = transport
    if cache_path is not None:
        _write_transport_cache(cache_path, version, transport)
    return AgTransport(transport, "help", version)


def select_transport(resolve_command: Callable[[], list[str] | None], env: Mapping[str, str], cwd: str,
                     binary: str, *, renew: Callable[[], None] | None = None) -> AgTransport:
    """PEERHUB_AG_TRANSPORT=argv forces argv; unset or empty detects; anything else is a configuration error."""
    forced = env.get(AG_TRANSPORT_ENV)
    if forced == "argv":
        return AgTransport("argv", "env", None)
    if forced:
        return AgTransport("invalid_config", "env", None, f"{AG_TRANSPORT_ENV} must be unset, empty or 'argv'")
    base = resolve_command()
    if base is None:
        return AgTransport("undetermined", "help", None, f"cannot determine agy stdin support: {common.executable_not_found(binary)}")
    return _detect_ag_transport(base, env, cwd, renew=renew)
