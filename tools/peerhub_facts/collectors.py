"""Probes that observe the world. Impure by design; no comparison here.

Every CLI probe goes through the same supervised pipe mechanism
``bootstrap.py``'s readiness probe uses (``PipeRunnerConfig`` +
``run_process`` + ``ProcessSupervisor``), never a bare unsupervised
``subprocess`` call -- required by the procedure, "What it checks" step 3.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from peerhub.adapters.contract import OutputChannel
from peerhub.adapters.registry import (
    ExecutableNotFoundError,
    ResolvedPeerTarget,
    resolve_peer_target,
)
from peerhub.dispatch.pipe import PipeRunnerConfig, run_process
from peerhub.dispatch.process import ProcessSupervisor

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# --- TOML narrowing --------------------------------------------------------
#
# ``tomllib`` hands back ``Any``. Both the pyproject read below and the
# contracts read in ``compare`` go through these, so a hand-edited file with
# the wrong shape degrades into "no expectation recorded" rather than a
# traceback that would be indistinguishable from the tool being broken.


def table(value: object) -> dict[str, object]:
    """Narrow a TOML value to a table, tolerating a missing/ill-typed key."""

    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return {}


def text(value: object, default: str) -> str:
    return value if isinstance(value, str) else default


def text_tuple(value: object, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(
            item for item in cast(list[object], value) if isinstance(item, str)
        )
    return default

#: Aliases the procedure requires resolving (step 1: "all 3 peer aliases").
PEER_ALIASES: tuple[str, ...] = ("ag", "cc", "cx")

_PROBE_TIMEOUT_MS = 30_000


@dataclass(frozen=True)
class PeerResolution:
    """Whether a peer's executable exists on this machine.

    ``absent_reason`` and ``failure_reason`` are deliberately separate. Only
    a missing local install is ABSENT; anything else that goes wrong during
    resolution (a broken adapter descriptor, a profile that no longer
    exists) is a structural failure and must not be laundered into "the
    contributor just doesn't have this CLI installed".
    """

    alias: str
    target: ResolvedPeerTarget | None
    absent_reason: str | None
    failure_reason: str | None = None

    @property
    def present(self) -> bool:
        return self.target is not None


@dataclass(frozen=True)
class ProbeResult:
    """One supervised CLI invocation."""

    command: str
    exit_code: int | None
    stdout_text: str
    error: str | None

    @property
    def ok(self) -> bool:
        return self.error is None and self.exit_code == 0


def resolve_peers(
    aliases: tuple[str, ...] = PEER_ALIASES,
) -> tuple[PeerResolution, ...]:
    """Resolve every alias, degrading gracefully when one is not installed.

    A missing local install is recorded as ABSENT and is explicitly not an
    error: a routine that required all three real CLIs would be unusable
    for most contributors (procedure step 2). Only ``ExecutableNotFoundError``
    means that, though -- every other resolution failure is structural and is
    reported as such, because silently calling a broken adapter "ABSENT"
    would hide exactly the drift this routine exists to catch.
    """

    resolutions: list[PeerResolution] = []
    for alias in aliases:
        try:
            target = resolve_peer_target(alias)
        except ExecutableNotFoundError as error:
            resolutions.append(PeerResolution(alias, None, str(error)))
        except Exception as error:  # noqa: BLE001 - reported, never fatal
            resolutions.append(
                PeerResolution(
                    alias,
                    None,
                    None,
                    failure_reason=f"{type(error).__name__}: {error}",
                )
            )
        else:
            resolutions.append(PeerResolution(alias, target, None))
    return tuple(resolutions)


def probe_cli(
    target: ResolvedPeerTarget,
    args: tuple[str, ...],
    *,
    timeout_ms: int = _PROBE_TIMEOUT_MS,
) -> ProbeResult:
    """Run one CLI invocation through the supervised pipe runner."""

    argv = (str(target.executable_path), *args)
    command = " ".join(argv)
    config = PipeRunnerConfig(
        argv=argv,
        cwd=target.executable_path.parent,
        process_timeout_ms=timeout_ms,
    )
    try:
        outcome = run_process(config, ProcessSupervisor())
    except Exception as error:  # noqa: BLE001 - probe failure is a reportable fact
        return ProbeResult(command, None, "", f"{type(error).__name__}: {error}")
    return ProbeResult(
        command=command,
        exit_code=outcome.execution_outcome.exit_code,
        stdout_text=outcome.canonical_stream.decode(errors="replace"),
        error=None,
    )


_SEMVER = re.compile(r"\b(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?)\b")

#: Lines a vendor prints *around* its version that must never be mistaken
#: for the version line itself. Codex in particular emits sandbox/config
#: warnings first, and those warnings carry paths that can contain
#: semver-shaped path segments.
_NOISE_MARKERS: tuple[str, ...] = (
    "warning",
    "warn:",
    "note:",
    "error",
    "deprecat",
    "sandbox",
)


@dataclass(frozen=True)
class _VersionGrammar:
    """How one vendor identifies its own version line."""

    #: Substrings (lowercased) that mark a line as this vendor's own
    #: version banner, e.g. ``codex-cli 0.147.0`` or ``2.1.222 (Claude Code)``.
    product_markers: tuple[str, ...]


#: Per-vendor grammars. The procedure requires vendor-specific parsing
#: rather than raw string comparison; each entry says what that vendor's
#: banner looks like, and a peer with no entry falls back to the shared
#: noise-filtered scan below (rather than crashing on an unknown peer).
_VERSION_GRAMMARS: dict[str, _VersionGrammar] = {
    "ag": _VersionGrammar(("agy", "antigravity")),
    "cc": _VersionGrammar(("claude",)),
    "cx": _VersionGrammar(("codex",)),
}


def parse_version(peer_kind: str, raw_output: str) -> str | None:
    """Extract a version vendor-specifically, not by raw string equality.

    Required by the procedure: a naive exact-match would report false drift
    whenever a vendor prints warnings or path noise around the real version
    line. The strategy is, in order:

    1. drop blank and vendor-noise lines (Codex's sandbox warnings);
    2. prefer a line carrying this vendor's own product marker, so
       ``codex-cli 0.147.0`` and ``2.1.222 (Claude Code)`` both resolve
       even when other numeric lines are present;
    3. otherwise take the first semver on a surviving line, which covers a
       bare ``0.1.2`` banner;
    4. never mine the discarded noise lines for a fallback version. A
       semver-shaped path or warning is not vendor-version evidence; an
       unrecognizable banner is reported as REVIEW_REQUIRED instead.
    """

    lines = [line.strip() for line in raw_output.splitlines() if line.strip()]
    quiet = [line for line in lines if not _is_noise(line)]
    grammar = _VERSION_GRAMMARS.get(peer_kind)

    if grammar is not None:
        for line in quiet:
            lowered = line.lower()
            if any(marker in lowered for marker in grammar.product_markers):
                match = _SEMVER.search(line)
                if match is not None:
                    return match.group(1)

    for line in quiet:
        match = _SEMVER.search(line)
        if match is not None:
            return match.group(1)

    # Do not fall back to warning/noise lines. Paths and diagnostic text can
    # contain semver-shaped fragments; presenting one as a measured vendor
    # version would be worse than the explicit REVIEW_REQUIRED produced for
    # an unparseable banner.
    return None


def _is_noise(line: str) -> bool:
    lowered = line.lower()
    return any(marker in lowered for marker in _NOISE_MARKERS)


def find_help_tokens(help_output: str, tokens: tuple[str, ...]) -> tuple[str, ...]:
    """Return the required tokens that are MISSING from help output.

    Semantic token presence, not exact-text comparison -- incidental
    wording or whitespace changes in vendor help text are not drift.
    """

    return tuple(
        token for token in tokens if not _contains_help_token(help_output, token)
    )


def _contains_help_token(help_output: str, token: str) -> bool:
    """Match a semantic CLI token, not an arbitrary substring.

    ``-p`` must not pass merely because help contains ``--permission`` and
    ``--output-format`` must not match ``--output-formatting``. Word-like
    subcommands (for example ``exec``) use the same identifier boundaries.
    """

    if not token:
        return False
    pattern = rf"(?<![0-9A-Za-z_-]){re.escape(token)}(?![0-9A-Za-z_-])"
    return re.search(pattern, help_output) is not None


# --- Decoder conformance ---------------------------------------------------
#
# These fixtures are transcribed from each adapter's own decoder source,
# not invented: agy reads a flat ``response`` key, claude reads
# ``result``/``is_error`` after slicing between the first ``{`` and last
# ``}`` (so a warning prefix is tolerated), and codex scans JSONL lines
# for ``type == "item.completed"`` with an ``agent_message`` item.

_CONFORMANCE_TEXT = "peerhub-facts-conformance"

_DECODER_FIXTURES: dict[str, bytes] = {
    "ag": json.dumps({"response": _CONFORMANCE_TEXT}).encode("utf-8"),
    "cc": (
        b"Warning: no stdin data received\n"
        + json.dumps(
            {"is_error": False, "result": _CONFORMANCE_TEXT}
        ).encode("utf-8")
    ),
    "cx": (
        json.dumps({"type": "session.created"}).encode("utf-8")
        + b"\n"
        + json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": _CONFORMANCE_TEXT},
            }
        ).encode("utf-8")
        + b"\n"
    ),
}

_DECODER_PROTOCOLS: dict[str, str] = {
    "ag": "flat-json",
    "cc": "claude-result-json",
    "cx": "jsonl-events",
}


@dataclass(frozen=True)
class DecoderConformance:
    """Result of feeding one protocol fixture through a real decoder."""

    peer_kind: str
    output_protocol: str
    fixture_digest_input: bytes
    observed_output_fields: tuple[str, ...]
    canonical_text: str | None
    event_kinds: tuple[str, ...]
    error: str | None


def decoder_conformance(
    peer_kind: str, *, payload: bytes | None = None
) -> DecoderConformance:
    """Feed the recorded protocol fixture through the real adapter decoder.

    Does not require the peer CLI to be installed -- this checks the
    decoder against a protocol shape, so it still runs for an ABSENT peer.

    ``payload`` overrides the recorded fixture. Production always uses the
    recorded one; the override exists so a test can push a *drifted*
    protocol shape through this exact code path rather than reimplementing
    it and proving nothing.
    """

    fixture = _DECODER_FIXTURES[peer_kind] if payload is None else payload
    protocol = _DECODER_PROTOCOLS.get(peer_kind, "unknown")
    observed_fields = _observed_output_fields(peer_kind, fixture)
    try:
        from peerhub.adapters.registry import resolve_peer_adapter

        adapter = resolve_peer_adapter(peer_kind)
        decoder = adapter.new_decoder(None)  # pyright: ignore[reportArgumentType]
        decoder.feed(fixture, channel=OutputChannel.STDOUT)
        decoded = decoder.finalize()
    except Exception as error:  # noqa: BLE001 - reportable, never fatal
        return DecoderConformance(
            peer_kind,
            protocol,
            fixture,
            observed_fields,
            None,
            (),
            f"{type(error).__name__}: {error}",
        )
    return DecoderConformance(
        peer_kind=peer_kind,
        output_protocol=protocol,
        fixture_digest_input=fixture,
        observed_output_fields=observed_fields,
        canonical_text=decoded.canonical_text,
        event_kinds=tuple(str(event.kind) for event in decoded.events),
        error=None,
    )


def _observed_output_fields(
    peer_kind: str, fixture: bytes
) -> tuple[str, ...]:
    """Read the top-level protocol fields present in a conformance fixture."""

    text_value = fixture.decode("utf-8", errors="replace")
    documents: list[object] = []
    try:
        if peer_kind == "cc":
            start = text_value.find("{")
            end = text_value.rfind("}")
            if start >= 0 and end >= start:
                documents.append(json.loads(text_value[start : end + 1]))
        elif peer_kind == "cx":
            documents.extend(
                json.loads(line)
                for line in text_value.splitlines()
                if line.strip()
            )
        else:
            documents.append(json.loads(text_value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()

    fields: set[str] = set()
    for document in documents:
        fields.update(table(document))
    return tuple(sorted(fields))


def expected_conformance_text() -> str:
    """The canonical text every decoder fixture should decode to."""

    return _CONFORMANCE_TEXT


# --- Dependencies ----------------------------------------------------------


@dataclass(frozen=True)
class DependencyObservation:
    """Declared constraint vs. what is actually installed.

    ``extra`` names the optional-dependency group a requirement came from
    (``None`` for a runtime dependency). A contributor who has not run
    ``pip install -e .[dev]`` is not experiencing drift, so the comparison
    side grades a missing *optional* requirement differently from a missing
    runtime one -- the same graceful-degradation reasoning as ABSENT peers.
    """

    requirement: str
    distribution: str
    installed_version: str | None
    satisfied: bool | None
    note: str
    extra: str | None = None


@dataclass(frozen=True)
class DependencySnapshot:
    observations: tuple[DependencyObservation, ...]
    pip_check_exit: int | None
    pip_check_output: str
    lockfile: str | None


_LOCKFILE_CANDIDATES = ("uv.lock", "poetry.lock", "requirements.lock", "pdm.lock")


def collect_dependencies(
    project_root: Path = PROJECT_ROOT,
) -> DependencySnapshot:
    """Read INSTALLED versions and evaluate them against pyproject specifiers.

    ``pyproject.toml`` holds declared constraints only, never what is
    actually installed -- the procedure calls this out explicitly as a
    correction to its own earlier draft.
    """

    import tomllib
    from importlib import metadata

    pyproject = table(
        tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    )
    project = table(pyproject.get("project"))
    #: (requirement string, originating extra or None for runtime)
    requirements: list[tuple[str, str | None]] = [
        (raw, None) for raw in text_tuple(project.get("dependencies"))
    ]
    for extra_name, extra_requirements in table(
        project.get("optional-dependencies")
    ).items():
        requirements.extend(
            (raw, extra_name) for raw in text_tuple(extra_requirements)
        )

    try:
        from packaging.requirements import Requirement  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - degrade to REVIEW_REQUIRED, never crash
        Requirement = None  # type: ignore[assignment]

    observations: list[DependencyObservation] = []
    for raw, extra in requirements:
        name = raw
        specifier = None
        if Requirement is not None:
            try:
                parsed = Requirement(raw)
                name = parsed.name
                specifier = parsed.specifier
            except Exception:  # noqa: BLE001
                specifier = None
        else:
            name = re.split(r"[<>=!~\[; ]", raw, maxsplit=1)[0]

        try:
            installed = metadata.version(name)
        except Exception:  # noqa: BLE001 - not installed is a real observation
            installed = None

        if installed is None:
            observations.append(
                DependencyObservation(
                    raw, name, None, False, "not installed", extra
                )
            )
            continue
        if specifier is None:
            observations.append(
                DependencyObservation(
                    raw,
                    name,
                    installed,
                    None,
                    "specifier not evaluated (packaging unavailable)",
                    extra,
                )
            )
            continue
        observations.append(
            DependencyObservation(
                raw,
                name,
                installed,
                installed in specifier,
                "evaluated against pyproject specifier",
                extra,
            )
        )

    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pip", "check"],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=project_root,
        )
        pip_exit: int | None = completed.returncode
        pip_out = (completed.stdout + completed.stderr).strip()
    except Exception as error:  # noqa: BLE001
        pip_exit = None
        pip_out = f"{type(error).__name__}: {error}"

    lockfile = next(
        (name for name in _LOCKFILE_CANDIDATES if (project_root / name).exists()),
        None,
    )
    return DependencySnapshot(tuple(observations), pip_exit, pip_out, lockfile)


# --- Repository / test suite ----------------------------------------------


def head_sha(project_root: Path = PROJECT_ROOT) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=project_root,
        )
    except Exception:  # noqa: BLE001
        return "unknown"
    if completed.returncode != 0:
        return "unknown"
    return completed.stdout.strip() or "unknown"


#: Counts are read token-wise rather than as one fixed-order regex: pytest
#: prints outcomes in its own order (``1 failed, 527 passed, 3 deselected``
#: -- failures first), so a pattern that assumed ``passed`` came first
#: silently reported ``passed=None`` on exactly the red runs that matter.
_COUNT_TOKEN = re.compile(
    r"(\d+)\s+(passed|failed|deselected|skipped|errors?|xfailed|xpassed)\b"
)
_DURATION = re.compile(r"\bin\s+(?P<duration>[\d.]+)s\b")

_COUNT_KEYS: tuple[str, ...] = ("passed", "failed", "deselected")


def parse_pytest_summary(output: str) -> dict[str, Any]:
    """Extract counts and duration from a ``pytest -q`` tail."""

    result: dict[str, Any] = {key: None for key in _COUNT_KEYS}
    result["duration"] = None

    for line in reversed(output.strip().splitlines()):
        duration = _DURATION.search(line)
        if duration is None:
            continue
        tokens = _COUNT_TOKEN.findall(line)
        if not tokens:
            continue
        for count, outcome in tokens:
            # Collection errors are counted as failures: "1 failed, 2 errors"
            # must not report failed=2 and lose the real failure.
            key = "failed" if outcome.startswith("error") else outcome
            if key in _COUNT_KEYS:
                result[key] = (result[key] or 0) + int(count)
        result["duration"] = float(duration.group("duration"))
        break
    return result


def run_test_suite(
    project_root: Path = PROJECT_ROOT,
    *,
    extra_args: tuple[str, ...] = (),
) -> tuple[dict[str, Any], str, int | None]:
    """Run a fresh ``pytest -q`` and return (counts, raw output, exit code)."""

    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *extra_args],
            capture_output=True,
            text=True,
            timeout=3600,
            cwd=project_root,
        )
    except Exception as error:  # noqa: BLE001
        return ({}, f"{type(error).__name__}: {error}", None)
    raw = completed.stdout + completed.stderr
    return (parse_pytest_summary(raw), raw, completed.returncode)
