"""Antigravity stdin transport discovery and caching."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable, Generator, Mapping, cast

from peerhub.core.load_limits import BUSY_PROBE_TIMEOUT_FACTOR, probe_timeout_factor
from peerhub.extensions.adapters import common
from peerhub.extensions.adapters.process import DEFAULT_MAX_BYTES, ProcessResult, SpawnFailure, decode_stderr

AG_TRANSPORT_ENV = "PEERHUB_AG_TRANSPORT"
_AG_PROBE_TIMEOUT_S = 10.0
_AG_TRANSPORT_MAX_AGE_S = 600.0
_AG_CACHE_LOCK_POLL_S = .1
_FileIdentity = tuple[str, str, int, int, int, int, int]
_TransportIdentity = tuple[_FileIdentity, ...]


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
    return os.path.normcase(str(path)), real, st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


_LAUNCHER_EXE = re.compile(r'^[ \t]*@?(?:call[ \t]+)?(?:"([^"\r\n]+\.exe)"|([^\s"]+\.exe))[ \t]+%\*[ \t]*\r?$',
                           re.IGNORECASE | re.MULTILINE)
_LAUNCHER_ENV = re.compile(r"%([^%]+)%")


def _transport_identity(base: list[str], env: Mapping[str, str]) -> _TransportIdentity | None:
    if len(base) != 1:
        return None  # a multi-argument command can run code independent of base[0]
    launcher = Path(base[0])
    if not launcher.is_absolute():
        return None  # PATH and child cwd can select a different file.
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
            age = time.time() - os.fstat(stream.fileno()).st_mtime
            if not 0 <= age < _AG_TRANSPORT_MAX_AGE_S:
                return None
            content: object = json.loads(stream.read(512))
    except (OSError, ValueError):
        return None
    if isinstance(content, dict):
        data = cast(dict[str, object], content)
        version, transport = data.get("version"), data.get("transport")
        if isinstance(version, str) and common.VERSION_RE.fullmatch(version) and transport in ("stream", "argv"):
            return AgTransport(cast(str, transport), "help", version)
    return None


def _try_cache_lock(file: BinaryIO) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    except OSError as exc:
        if os.name == "nt" and exc.errno in (errno.EACCES, errno.EDEADLK):
            return False
        raise
    return True


@contextmanager
def _transport_cache_lock(path: Path, renew: Callable[[], None] | None) -> Generator[bool, None, None]:
    """Serialize cold detection for one identity, including across CLI processes."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.with_suffix(".lock").open("a+b")
    except OSError:
        yield False
        return
    with lock:
        # Two probes and one shared retry. Keep the lease alive while another process probes.
        deadline = time.monotonic() + 3 * _AG_PROBE_TIMEOUT_S * BUSY_PROBE_TIMEOUT_FACTOR
        next_renewal = time.monotonic()
        while True:
            try:
                acquired = _try_cache_lock(lock)
            except OSError:
                acquired = False
                break
            now = time.monotonic()
            if acquired or now >= deadline:
                break
            if renew is not None and now >= next_renewal:
                renew()
                next_renewal = now + _AG_PROBE_TIMEOUT_S
            time.sleep(min(_AG_CACHE_LOCK_POLL_S, deadline - now))
        yield acquired


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
    try:
        identity = _transport_identity(base, env)
        path = _transport_cache_path(identity, env) if identity is not None else None
    except (OSError, RuntimeError):
        identity, path = None, None
    if path is None:
        return _probe_transport(base, env, cwd, renew)
    if cached := _read_transport_cache(path):
        return cached
    with _transport_cache_lock(path, renew) as acquired:
        try:
            same_identity = _transport_identity(base, env) == identity
        except (OSError, RuntimeError):
            same_identity = False
        if same_identity:
            if cached := _read_transport_cache(path):
                return cached
        result = _probe_transport(base, env, cwd, renew)
        if same_identity and acquired and result.version is not None and result.error is None:
            try:
                if _transport_identity(base, env) == identity:
                    _write_transport_cache(path, result.version, result.transport)
            except (OSError, RuntimeError):
                pass  # The executable or launcher changed during detection.
        return result


def _probe_transport(base: list[str], env: Mapping[str, str], cwd: str,
                     renew: Callable[[], None] | None) -> AgTransport:
    def undetermined(why: str, version: str | None = None) -> AgTransport:
        return AgTransport("undetermined", "help", version, f"cannot determine agy stdin support: {why}")

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
