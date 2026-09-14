"""Cross-process maintenance exclusion, independent of the replaceable database.

Connections hold a shared OS lock until close; maintenance takes an exclusive,
nonblocking lock. The OS releases locks on process death. A durable journal
blocks subsequent use after a restore crash even though its OS lock is gone.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import BinaryIO, Self


class WorkspaceMaintenanceError(RuntimeError):
    """The workspace is busy or requires explicit restore recovery."""


def reject_redirected(path: Path) -> None:
    """Reject links/reparse points below an explicitly selected root.

    Callers check every member's ancestors within that root separately. Host
    drive/path aliases above the selected workspace remain supported.
    """
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise WorkspaceMaintenanceError(f"redirected path refused: {path}")


def require_recovered(home: Path) -> None:
    journal = home / "restore-journal.json"
    reject_redirected(journal)
    if journal.exists():
        try:
            raw: object = json.loads(journal.read_text(encoding="utf-8"))
            phase = None
            if isinstance(raw, dict):
                candidate = raw.get("phase")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                if isinstance(candidate, str):
                    phase = candidate
            complete = phase in ("completed", "rolled_back")
        except (ValueError, OSError):
            complete = False
        if not complete:
            raise WorkspaceMaintenanceError(
                f"recovery-required: inspect {journal}; run recover_workspace_restore()"
            )


class WorkspaceGuard:
    """Shared access or exclusive maintenance for a database's containing home."""

    def __init__(
        self, home: Path, *, exclusive: bool = False, readonly: bool = False,
        recovery: bool = False,
    ) -> None:
        self.home = home
        self.exclusive = exclusive
        self.readonly = readonly
        self.recovery = recovery
        self._handle: BinaryIO | None = None

    def __enter__(self) -> Self:
        reject_redirected(self.home)
        path = self.home / "maintenance.lock"
        reject_redirected(path)
        if not self.recovery:
            require_recovered(self.home)
        if self.readonly and not path.exists():
            # Legacy stores remain inspectable without creating any files.
            return self
        if not self.readonly:
            self.home.mkdir(parents=True, exist_ok=True)
        handle = path.open("rb" if self.readonly else "a+b")
        try:
            if os.name == "nt":
                import ctypes
                import msvcrt
                from ctypes import wintypes

                class Overlapped(ctypes.Structure):
                    _fields_ = [
                        ("Internal", ctypes.c_size_t),
                        ("InternalHigh", ctypes.c_size_t),
                        ("Offset", wintypes.DWORD),
                        ("OffsetHigh", wintypes.DWORD),
                        ("hEvent", wintypes.HANDLE),
                    ]

                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                lock = kernel.LockFileEx
                lock.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.DWORD,
                                 ctypes.POINTER(Overlapped)]
                lock.restype = wintypes.BOOL
                overlapped = Overlapped()
                if not lock(msvcrt.get_osfhandle(handle.fileno()),
                            1 | (2 if self.exclusive else 0), 0, 1, 0,
                            ctypes.byref(overlapped)):
                    raise WorkspaceMaintenanceError(f"workspace busy: {self.home}")
            else:
                import fcntl
                try:
                    fcntl.flock(handle, (fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH)
                                | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise WorkspaceMaintenanceError(f"workspace busy: {self.home}") from exc
            if not self.recovery:
                require_recovered(self.home)
        except BaseException:
            handle.close()
            raise
        self._handle = handle
        return self

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __exit__(self, *args: object) -> None:
        self.close()
