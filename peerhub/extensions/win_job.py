"""Win32 Job Objects for suspended quota probes."""

from __future__ import annotations

import ctypes
from contextlib import suppress
from ctypes import wintypes
from typing import Callable

CREATE_SUSPENDED = 0x00000004
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_TERMINATION_EXIT_CODE = 1


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    # Windows declares all six IO_COUNTERS fields as ULONGLONG on both architectures.
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class ProbeResumeError(OSError):
    """The probe could not be started safely."""


def resolve_resume_process() -> Callable[[int], None]:
    try:
        resume = getattr(ctypes, "WinDLL")("ntdll").NtResumeProcess
        resume.argtypes = [wintypes.HANDLE]
        resume.restype = ctypes.c_long  # NTSTATUS
    except (AttributeError, OSError) as exc:
        raise ProbeResumeError(f"NtResumeProcess unavailable ({exc})") from exc

    def resume_process(process_handle: int) -> None:
        try:
            status: int = resume(process_handle)
        except (OSError, ctypes.ArgumentError) as exc:
            raise ProbeResumeError(f"NtResumeProcess failed ({exc})") from exc
        if status != 0:
            raise ProbeResumeError(f"NtResumeProcess failed (NTSTATUS 0x{ctypes.c_uint32(status).value:08X})")

    return resume_process


def _last_error(api: str) -> OSError:
    code: int = getattr(ctypes, "get_last_error")()
    return OSError(f"{api} failed (WinError {code})")


class Job:
    """An unnamed, non-inheritable job assigned to an existing process handle."""

    def __init__(self, process_handle: int) -> None:
        kernel: ctypes.CDLL = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
        create = kernel.CreateJobObjectW
        create.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        create.restype = wintypes.HANDLE
        set_info = kernel.SetInformationJobObject
        set_info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        set_info.restype = wintypes.BOOL
        assign = kernel.AssignProcessToJobObject
        assign.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        assign.restype = wintypes.BOOL
        self._terminate = kernel.TerminateJobObject
        self._terminate.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self._terminate.restype = wintypes.BOOL
        self._close = kernel.CloseHandle
        self._close.argtypes = [wintypes.HANDLE]
        self._close.restype = wintypes.BOOL

        handle: int | None = create(None, None)
        if not handle:
            raise _last_error("CreateJobObjectW")
        self._handle = handle
        try:
            limits = _ExtendedLimitInformation()
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not set_info(handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise _last_error("SetInformationJobObject")
            if not assign(handle, process_handle):
                raise _last_error("AssignProcessToJobObject")
        except BaseException:
            with suppress(Exception):
                self._close(handle)
            raise

    def terminate_and_close(self) -> None:
        try:
            with suppress(Exception):
                self._terminate(self._handle, _JOB_TERMINATION_EXIT_CODE)
        finally:
            with suppress(Exception):
                self._close(self._handle)
