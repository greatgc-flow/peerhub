"""Oversized-prompt staging and reference resolution.

Ratified backlog item H (``docs/reviews/p-drive-mece-migration-audit-
2026-09-09.md`` section 5): ``AdapterRequest`` has always carried a
``prompt_reference`` field, but nothing ever set it -- the ask path raised
``ValueError`` once an assembled prompt passed ``max_inline_utf8_bytes``,
so a large-context ask simply failed.

Parity target is ``hub_peer.py``'s ``AgyAdapter.prepare_input``: never
truncate, stage the full UTF-8 payload to a file, verify a digest
round-trip, and hand the peer a pointer to it. What differs here is
peerhub's own idiom rather than hub.py's:

* the staging directory is workspace-relative and validated the same way
  ``dispatch/artifacts.py`` validates ``artifact_staging_relative_root``
  (relative, no ``..``, must resolve inside the workspace root), and it
  defaults under the workspace's ``.peerhub`` state home rather than a
  bare OS temp directory;
* the filename is a SHA-256 of stable identity rather than a raw
  caller-supplied id used as a path segment -- ``artifacts.py``'s
  documented safety mechanism #2;
* the reference travels on ``AdapterRequest.prompt_reference``, so the
  ``exactly one of prompt_content/prompt_reference`` contract invariant
  holds, instead of being smuggled inside the prompt text.

Adapters call :func:`resolve_prompt_payload` in place of reading
``prompt_content`` directly. An adapter whose ``PromptPolicy`` sets
``artifact_reference_supported`` may instead pass ``prompt_reference``
through to its vendor CLI natively; the pointer text here is the default
emulation for the vendors that have no such flag, which today is all of
them.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path

from peerhub.adapters.contract import AdapterRequest

_STAGED_PROMPT_SUFFIX = ".prompt.txt"
_STAGED_NAME_HEX_CHARS = 32


@dataclass(frozen=True)
class StagedPrompt:
    """One prompt payload durably written outside the command line."""

    path: Path
    sha256_hex: str
    utf8_bytes: int
    characters: int


def resolve_staging_dir(root: Path, relative_dir: str) -> Path:
    """Resolve and validate the staging directory under ``root``.

    ``root`` is whichever base ``PromptStagingConfig.location`` selected
    (the workspace root, or the OS temp root via
    ``config_paths.resolve_workspace_temp()``) -- the validation itself
    never changes regardless of which root is in play (item 3, dotdir
    consolidation, ratified 2026-09-09): the configured location must be
    relative, must not traverse, and must resolve inside ``root``. Never
    relaxed to accept an absolute ``relative_dir``.
    """

    relative = Path(relative_dir)
    if relative.is_absolute():
        raise ValueError(
            f"prompt staging directory must be relative, got: {relative}"
        )
    if ".." in relative.parts:
        raise ValueError(
            f"prompt staging directory cannot contain traversal '..': "
            f"{relative}"
        )
    resolved_root = root.resolve()
    staging_dir = (resolved_root / relative).resolve()
    try:
        staging_dir.relative_to(resolved_root)
    except ValueError:
        raise ValueError(
            f"prompt staging directory {staging_dir} resolves outside "
            f"root {resolved_root}"
        ) from None
    return staging_dir


def _staged_filename(request_id: str, payload_digest: str) -> str:
    identity = f"{request_id}:{payload_digest}".encode("utf-8")
    return (
        hashlib.sha256(identity).hexdigest()[:_STAGED_NAME_HEX_CHARS]
        + _STAGED_PROMPT_SUFFIX
    )


def stage_prompt(
    prompt: str,
    *,
    root: Path,
    relative_dir: str,
    request_id: str,
) -> StagedPrompt:
    """Write ``prompt`` to the staging directory under ``root``, verbatim.

    ``root`` is whichever base ``PromptStagingConfig.location`` selected.
    The payload is never truncated. The write is fsynced and read back for
    a digest round-trip before the path is returned; a partial or corrupt
    write removes only this call's own file and re-raises.
    """

    payload = prompt.encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()

    staging_dir = resolve_staging_dir(root, relative_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    staged_path = staging_dir / _staged_filename(request_id, digest)

    if staged_path.is_file():
        # Deterministic identity: the same request re-staging the same bytes
        # (a retried attempt) reuses the verified file instead of colliding.
        existing = staged_path.read_bytes()
        if hashlib.sha256(existing).hexdigest() == digest:
            return StagedPrompt(
                path=staged_path,
                sha256_hex=digest,
                utf8_bytes=len(payload),
                characters=len(prompt),
            )
        staged_path.unlink()

    try:
        with staged_path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        round_trip = staged_path.read_bytes()
        if (
            len(round_trip) != len(payload)
            or hashlib.sha256(round_trip).hexdigest() != digest
        ):
            raise OSError("staged prompt failed its UTF-8 digest round-trip")
    except BaseException:
        staged_path.unlink(missing_ok=True)
        raise

    return StagedPrompt(
        path=staged_path,
        sha256_hex=digest,
        utf8_bytes=len(payload),
        characters=len(prompt),
    )


def remove_staged_prompt(reference: str) -> None:
    """Remove one staged prompt file, once its dispatch is definitely done.

    Item 3 (dotdir consolidation, ratified 2026-09-09): the cleanup owner
    for a staged prompt is the dispatch that created it, once that
    dispatch's outcome is no longer uncertain (success, definite failure,
    or cancellation -- never call this for an ``ExecutionCertainty
    .MAY_HAVE_STARTED`` / ``RequestState.START_UNCERTAIN`` outcome, since
    the supervised process might still read the file). Missing is not an
    error -- idempotent, safe to call more than once.
    """

    Path(reference).unlink(missing_ok=True)


def sweep_stale_staged_prompts(root: Path, relative_dir: str, *, max_age_seconds: float) -> int:
    """Bounded startup janitor: reclaim staged prompts left behind by a
    dispatch whose outcome was genuinely uncertain (item 3's "a bounded
    startup janitor reclaims genuinely-uncertain cases").

    Synchronous, on-demand -- called opportunistically at the start of a
    later dispatch, not a background daemon/thread (matching this
    codebase's existing one-shot-sweep idiom, e.g.
    ``LessonService.sweep_expired()``). Only files older than
    ``max_age_seconds`` are removed, so a file from a dispatch that is
    merely slow (not actually abandoned) is left alone. Returns the count
    removed.
    """

    staging_dir = resolve_staging_dir(root, relative_dir)
    if not staging_dir.is_dir():
        return 0
    now = time.time()
    removed = 0
    for entry in staging_dir.iterdir():
        if not entry.is_file() or not entry.name.endswith(_STAGED_PROMPT_SUFFIX):
            continue
        try:
            age = now - entry.stat().st_mtime
        except OSError:
            continue
        if age >= max_age_seconds:
            entry.unlink(missing_ok=True)
            removed += 1
    return removed


def render_staged_prompt_pointer(reference: str) -> str:
    """Render the instruction that points a peer at a staged payload file.

    The digest and lengths are recomputed from the file itself rather than
    trusted from the caller, so the pointer a peer receives always
    describes the bytes actually on disk.
    """

    path = Path(reference)
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    text = payload.decode("utf-8")
    return (
        "[IPC PAYLOAD FILE]\n"
        "The complete request is stored in the UTF-8 file:\n"
        f"{path}\n"
        "Read the entire file before answering. Treat all of its contents "
        "as the complete request. Do not truncate it or substitute prior "
        "conversation context.\n"
        f"UTF-8 bytes: {len(payload)}; characters: {len(text)}; "
        f"SHA-256: {digest}"
    )


def resolve_prompt_payload(request: AdapterRequest) -> str:
    """Return the prompt text an adapter should place on its invocation.

    Inline content passes through unchanged. A staged reference becomes the
    pointer instruction above -- the payload itself is never inlined back,
    which is the whole point of having staged it.
    """

    if request.prompt_content is not None:
        return request.prompt_content
    if request.prompt_reference is not None:
        return render_staged_prompt_pointer(request.prompt_reference)
    raise ValueError("prompt_content or prompt_reference is required")
