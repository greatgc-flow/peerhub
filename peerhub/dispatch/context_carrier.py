"""D-CTX context-file carrier: secure creation, delivery, and cleanup of
the per-attempt file that carries a dispatch credential to a peer CLI
subprocess (docs/design/peerhub-dctx-proposal-1-2026-09-13.md section 3.2,
D5/D6, D-CTX D0/Q2/D2 closed 2026-09-14).

The file is a CARRIER, not authority (D1): it holds a credential_id that
``peerhub.persistence.dispatch_context.verify_credential`` checks for
table membership in the workspace's own database. Losing or leaking this
file is equivalent to leaking the credential it names, so:

- D5: lives under the workspace's own transient OS-temp namespace
  (``resolve_workspace_temp``, already used by R3's restore staging) at
  a per-attempt, unpredictable path -- never a well-known singleton
  (concurrent dispatches would collide) and never
  ``.peerhub/run/contexts`` (that would violate the 2026-09-09 durable/
  transient separation this project already ratified).
- D6 (measured, not assumed): a real Windows ACL probe on this project's
  own actual temp directory (2026-09-14) found the OS default grants
  ``NT AUTHORITY\\Authenticated Users:(M)`` and ``BUILTIN\\Users:(RX)`` --
  i.e. every authenticated local account can modify or read a freshly
  created file there by default. Relying on that default would provide
  none of the "account boundary" protection D3 describes. This module
  therefore explicitly narrows the ACL to the current user via ``icacls``
  (a well-tested OS tool, chosen over hand-rolled ctypes SID/ACL
  construction, which is easy to get subtly wrong) immediately after
  creation, on Windows; ``os.chmod(0o600)`` on POSIX. Exclusive creation
  (``open(..., "x")``) plus a pre-check that the containing directory is
  not a symlink/reparse point closes the creation-race D6 also calls out.

This narrows exposure to OTHER OS accounts on the same host. It does
**not** defend against another process running as the SAME OS account
(D3's already-ratified scope boundary) -- that process could read this
file, or the workspace database the credential is verified against,
equally either way.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from peerhub.application.config_paths import resolve_workspace_temp
from peerhub.persistence.maintenance import reject_redirected

_CONTEXTS_DIRNAME = "dispatch-contexts"


class ContextFileError(RuntimeError):
    """The context file could not be created, read, or is malformed."""


def _restrict_to_current_user(path: Path) -> None:
    """Narrow a freshly created file's permissions to the current OS
    account only. Best-effort is not acceptable here: a failure to
    restrict must fail the whole creation, since an unrestricted file is
    exactly the exposure this exists to prevent."""

    if sys.platform == "win32":
        username = os.environ.get("USERNAME") or ""
        if not username:
            raise ContextFileError(
                "cannot determine current user to restrict context file ACL"
            )
        domain_user = f"{os.environ.get('USERDOMAIN', '')}\\{username}".lstrip("\\")
        result = subprocess.run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"{domain_user}:F",
            ],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise ContextFileError(
                f"icacls failed to restrict context file permissions: "
                f"{result.stderr.decode(errors='replace')}"
            )
    else:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def create_context_file(
    workspace_root: Path,
    *,
    credential_id: str,
    temp_root: Path | None = None,
) -> Path:
    """Create one per-attempt context file and return its path.

    Exclusive creation plus a pre-check that the containing directory is
    not a symlink/reparse point closes the race a concurrently-running
    attacker process would need to win to substitute its own file at a
    guessable path (D6). The random filename component means there is no
    guessable path to substitute in the first place.
    """

    contexts_dir = resolve_workspace_temp(workspace_root, temp_root=temp_root).path / _CONTEXTS_DIRNAME
    reject_redirected(contexts_dir)
    contexts_dir.mkdir(parents=True, exist_ok=True)
    reject_redirected(contexts_dir)

    path = contexts_dir / f"{secrets.token_urlsafe(24)}.json"
    payload = json.dumps(
        {"credential_id": credential_id, "workspace_root": str(workspace_root)}
    )
    try:
        with open(path, "x", encoding="utf-8") as f:
            f.write(payload)
    except FileExistsError:
        # secrets.token_urlsafe(24) collision is not a real-world event;
        # treat it as evidence of tampering rather than silently retrying
        # with a new name, which could mask a substitution attempt.
        raise ContextFileError(f"context file path already exists: {path}") from None
    try:
        _restrict_to_current_user(path)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def read_context_file(path: Path) -> tuple[str, str]:
    """Return (credential_id, workspace_root) from a context file.

    Fails closed: any malformed, missing, or wrong-shaped file raises
    rather than returning a partial or default result.
    """

    reject_redirected(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContextFileError(f"cannot read context file {path}: {exc}") from exc
    try:
        payload: object = json.loads(raw)
    except ValueError as exc:
        raise ContextFileError(f"context file {path} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ContextFileError(f"context file {path} must contain a JSON object")
    fields = cast("dict[str, Any]", payload)
    credential_id = fields.get("credential_id")
    workspace_root = fields.get("workspace_root")
    if not isinstance(credential_id, str) or not credential_id:
        raise ContextFileError(f"context file {path} missing credential_id")
    if not isinstance(workspace_root, str) or not workspace_root:
        raise ContextFileError(f"context file {path} missing workspace_root")
    return (credential_id, workspace_root)


def cleanup_context_file(path: Path) -> None:
    """Best-effort removal. Losing this file after dispatch completes
    (or fails before this runs) is not itself a new exposure -- the
    credential row's own expires_at/revoked_at are the actual lifetime
    boundary; this is normal janitorial cleanup, not a security control."""

    path.unlink(missing_ok=True)
