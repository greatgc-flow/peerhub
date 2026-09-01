"""Real TreeController implementation for OS-level process tree supervision.

Ratified design: SLICE5-KICKOFF-R1.md "DT-03/DT-04/DT-05 contract RATIFIED".

OS mapping summary:
Windows:
- bind_spawn: CreateJobObjectW + AssignProcessToJobObject.
- soft_cancel: GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT) if available.
- terminate_tree: No safe generic graceful-tree primitive on Windows; record unavailable.
- kill_tree: TerminateJobObject on Job Object (fallback: TerminateProcess).
- observe_tree: QueryInformationJobObject (or process liveness) + atomic creation time verification.

POSIX:
- bind_spawn: Record process group (pgid = os.getpgid(pid)).
- soft_cancel: os.killpg(pgid, signal.SIGINT).
- terminate_tree: os.killpg(pgid, signal.SIGTERM).
- kill_tree: os.killpg(pgid, signal.SIGKILL).
- observe_tree: os.kill(pid, 0) + creation time verification.

NOTE (c316f6d / SLICE5-KICKOFF-R1.md):
Windows Job Object binding occurs after Popen. There is a known spawn-race limitation
where child processes spawned by the root process prior to AssignProcessToJobObject
may escape Job Object containment. RECONCILE_TREE re-observation is the accepted
safety net for an escaped child, not a guarantee of prevention.
"""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
from dataclasses import dataclass

from peerhub.dispatch.contract import ProcessBirthIdentity
from peerhub.dispatch.pipe import TreeHandle, TreeDispatchReceipt
from peerhub.dispatch.process import ObservationState, TreeProcessObservation


@dataclass
class ProcessTreeHandle:
    """Concrete implementation of TreeHandle representing a managed process tree."""

    root_identity: ProcessBirthIdentity
    process: subprocess.Popen[bytes] | None = None
    win_job_handle: int | None = None
    posix_pgid: int | None = None

    def __del__(self) -> None:
        if sys.platform == "win32" and self.win_job_handle is not None:
            try:
                kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                kernel32.CloseHandle(self.win_job_handle)
            except Exception:
                pass

if sys.platform == "win32":

    class JOBOBJECT_BASIC_PROCESS_ID_LIST(ctypes.Structure):
        _fields_ = [
            ("NumberOfAssignedProcesses", ctypes.c_uint32),
            ("NumberOfProcessIdsInList", ctypes.c_uint32),
            ("ProcessIdList", ctypes.c_size_t * 1024),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
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

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


def _get_process_creation_time_ms(pid: int) -> int:
    """Get process creation time in milliseconds since epoch.

    On Windows, uses Win32 GetProcessTimes via ctypes to convert FILETIME
    to UNIX epoch milliseconds without external dependencies.
    Fallback to psutil if available, or 0.
    """
    if pid <= 0:
        return 0

    if sys.platform == "win32":
        try:
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000, PROCESS_QUERY_INFORMATION = 0x0400
            h_proc = kernel32.OpenProcess(0x1000, False, pid)
            if not h_proc:
                h_proc = kernel32.OpenProcess(0x0400, False, pid)
            if h_proc:
                try:
                    creation_time = ctypes.c_uint64()
                    exit_time = ctypes.c_uint64()
                    kernel_time = ctypes.c_uint64()
                    user_time = ctypes.c_uint64()
                    res = kernel32.GetProcessTimes(
                        h_proc,
                        ctypes.byref(creation_time),
                        ctypes.byref(exit_time),
                        ctypes.byref(kernel_time),
                        ctypes.byref(user_time),
                    )
                    if res:
                        filetime = creation_time.value
                        # 116444736000000000 is 100-ns intervals between 1601 and 1970
                        if filetime > 116444736000000000:
                            return (filetime - 116444736000000000) // 10000
                finally:
                    kernel32.CloseHandle(h_proc)
        except Exception:
            pass

    try:
        import psutil  # type: ignore[import-untyped]

        p = psutil.Process(pid)
        return int(p.create_time() * 1000)
    except Exception:
        pass

    return 0


def verify_process_identity(
    pid: int,
    expected_identity: ProcessBirthIdentity,
) -> tuple[bool, int]:
    """Verify PID identity atomically against expected ProcessBirthIdentity.

    Returns (is_verified, actual_creation_time_ms).
    """
    if pid <= 0 or pid != expected_identity.pid:
        return False, 0

    actual_creation_time = _get_process_creation_time_ms(pid)
    expected_creation_time = expected_identity.process_creation_time

    if expected_creation_time > 0 and actual_creation_time > 0:
        if abs(actual_creation_time - expected_creation_time) > 100:
            return False, actual_creation_time

    return True, actual_creation_time


def _is_win_process_active(pid: int) -> bool:
    """Check if a Windows process is active (exit code STILL_ACTIVE / 259)."""
    if sys.platform != "win32":
        return False
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        h_proc = kernel32.OpenProcess(0x1000, False, pid)
        if not h_proc:
            h_proc = kernel32.OpenProcess(0x0400, False, pid)
        if not h_proc:
            return False
        try:
            exit_code = ctypes.c_uint32()
            res = kernel32.GetExitCodeProcess(h_proc, ctypes.byref(exit_code))
            if res:
                return exit_code.value == 259
        finally:
            kernel32.CloseHandle(h_proc)
    except Exception:
        pass
    return False


def _is_posix_process_active(pid: int) -> bool:
    """Check if a POSIX process is active via os.kill(pid, 0)."""
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


class RealTreeController:
    """Production TreeController satisfying the Protocol in peerhub.dispatch.pipe.

    Implements identity-verified process tree supervision and signal delivery
    for Windows and POSIX operating systems per the ratified OS-mapping table.
    """

    def bind_spawn(  # pyright: ignore[reportUnknownParameterType]
        self,
        *,
        process: subprocess.Popen[bytes],
        root: ProcessBirthIdentity,
    ) -> TreeHandle:
        """Bind a spawned process to a tree controller handle.

        On Windows, creates a Job Object and assigns the process to it.
        On POSIX, records the process group ID.
        """
        win_job_handle: int | None = None
        posix_pgid: int | None = None

        if sys.platform == "win32":
            try:
                kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                job = kernel32.CreateJobObjectW(None, None)
                if job:
                    # Set JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE (0x2000) so children
                    # die automatically if the parent process abruptly exits
                    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
                    info.BasicLimitInformation.LimitFlags = 0x2000
                    kernel32.SetInformationJobObject(
                        job,
                        9,  # JobObjectExtendedLimitInformation
                        ctypes.byref(info),
                        ctypes.sizeof(info),
                    )

                    proc_handle = getattr(process, "_handle", None)
                    if proc_handle:
                        res = kernel32.AssignProcessToJobObject(job, proc_handle)
                        if res:
                            win_job_handle = job
                        else:
                            kernel32.CloseHandle(job)
            except Exception:
                pass
        else:
            try:
                posix_pgid = os.getpgid(process.pid)
            except Exception:
                posix_pgid = process.pid

        return ProcessTreeHandle(
            root_identity=root,
            process=process,
            win_job_handle=win_job_handle,
            posix_pgid=posix_pgid,
        )

    def soft_cancel(self, tree: TreeHandle) -> TreeDispatchReceipt:  # pyright: ignore[reportUnknownParameterType]
        """Attempt graceful cancellation (SOFT_CANCEL).

        Windows: Sends CTRL_BREAK_EVENT if console is available; records unavailable otherwise.
        POSIX: Sends SIGINT to process group.
        """
        root = tree.root_identity  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        verified, _ = verify_process_identity(root.pid, root)  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
        if not verified:
            return TreeDispatchReceipt(  # pyright: ignore[reportUnknownVariableType]
                dispatched=False,
                signal_name="SOFT_CANCEL",
                target_identities=(),
            )

        if sys.platform == "win32":
            try:
                kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                # CTRL_BREAK_EVENT = 1
                res = kernel32.GenerateConsoleCtrlEvent(1, root.pid)  # pyright: ignore[reportUnknownMemberType]
                if res:
                    return TreeDispatchReceipt(  # pyright: ignore[reportUnknownVariableType]
                        dispatched=True,
                        signal_name="CTRL_BREAK_EVENT",
                        target_identities=(root,),
                    )
            except Exception:
                pass
            return TreeDispatchReceipt(  # pyright: ignore[reportUnknownVariableType]
                dispatched=False,
                signal_name="CTRL_BREAK_EVENT",
                target_identities=(),
            )
        else:
            pgid = getattr(tree, "posix_pgid", None) or root.pid
            try:
                os.killpg(pgid, signal.SIGINT)
                return TreeDispatchReceipt(
                    dispatched=True,
                    signal_name="SIGINT",
                    target_identities=(root,),
                )
            except Exception:
                try:
                    os.kill(root.pid, signal.SIGINT)
                    return TreeDispatchReceipt(
                        dispatched=True,
                        signal_name="SIGINT",
                        target_identities=(root,),
                    )
                except Exception:
                    return TreeDispatchReceipt(
                        dispatched=False,
                        signal_name="SIGINT",
                        target_identities=(),
                    )

    def terminate_tree(self, tree: TreeHandle) -> TreeDispatchReceipt:  # pyright: ignore[reportUnknownParameterType]
        """Attempt process tree termination (TERMINATE_TREE).

        Windows: No safe generic graceful-tree primitive; records unavailable per ratified spec.
        POSIX: Sends SIGTERM to process group.
        """
        root = tree.root_identity  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        verified, _ = verify_process_identity(root.pid, root)  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
        if not verified:
            return TreeDispatchReceipt(  # pyright: ignore[reportUnknownVariableType]
                dispatched=False,
                signal_name="TERMINATE_TREE",
                target_identities=(),
            )

        if sys.platform == "win32":
            # Per ratified table: no safe generic graceful-tree primitive on Windows.
            # Record unavailable and advance immediately.
            return TreeDispatchReceipt(  # pyright: ignore[reportUnknownVariableType]
                dispatched=False,
                signal_name="TERMINATE_TREE",
                target_identities=(),
            )
        else:
            pgid = getattr(tree, "posix_pgid", None) or root.pid
            try:
                os.killpg(pgid, signal.SIGTERM)
                return TreeDispatchReceipt(
                    dispatched=True,
                    signal_name="SIGTERM",
                    target_identities=(root,),
                )
            except Exception:
                try:
                    os.kill(root.pid, signal.SIGTERM)
                    return TreeDispatchReceipt(
                        dispatched=True,
                        signal_name="SIGTERM",
                        target_identities=(root,),
                    )
                except Exception:
                    return TreeDispatchReceipt(
                        dispatched=False,
                        signal_name="SIGTERM",
                        target_identities=(),
                    )

    def kill_tree(self, tree: TreeHandle) -> TreeDispatchReceipt:  # pyright: ignore[reportUnknownParameterType]
        """Forcibly kill process tree (KILL_TREE).

        Windows: Calls TerminateJobObject on Job Object, or TerminateProcess on root PID.
        POSIX: Sends SIGKILL to process group.
        """
        root = tree.root_identity  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        verified, _ = verify_process_identity(root.pid, root)  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
        if not verified:
            return TreeDispatchReceipt(  # pyright: ignore[reportUnknownVariableType]
                dispatched=False,
                signal_name="KILL_TREE",
                target_identities=(),
            )

        if sys.platform == "win32":
            job_handle = getattr(tree, "win_job_handle", None)  # pyright: ignore[reportUnknownArgumentType]
            if job_handle is not None:
                try:
                    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                    res = kernel32.TerminateJobObject(job_handle, 1)
                    if res:
                        return TreeDispatchReceipt(  # pyright: ignore[reportUnknownVariableType]
                            dispatched=True,
                            signal_name="TerminateJobObject",
                            target_identities=(root,),
                        )
                except Exception:
                    pass

            # Fallback: individually identity-verified TerminateProcess
            try:
                kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                # PROCESS_TERMINATE = 0x0001
                h_proc = kernel32.OpenProcess(0x0001, False, root.pid)  # pyright: ignore[reportUnknownMemberType]
                if h_proc:
                    try:
                        res = kernel32.TerminateProcess(h_proc, 1)
                        if res:
                            return TreeDispatchReceipt(  # pyright: ignore[reportUnknownVariableType]
                                dispatched=True,
                                signal_name="TerminateProcess",
                                target_identities=(root,),
                            )
                    finally:
                        kernel32.CloseHandle(h_proc)
            except Exception:
                pass

            return TreeDispatchReceipt(  # pyright: ignore[reportUnknownVariableType]
                dispatched=False,
                signal_name="KILL_TREE",
                target_identities=(),
            )
        else:
            pgid = getattr(tree, "posix_pgid", None) or root.pid
            try:
                os.killpg(pgid, signal.SIGKILL)
                return TreeDispatchReceipt(
                    dispatched=True,
                    signal_name="SIGKILL",
                    target_identities=(root,),
                )
            except Exception:
                try:
                    os.kill(root.pid, signal.SIGKILL)
                    return TreeDispatchReceipt(
                        dispatched=True,
                        signal_name="SIGKILL",
                        target_identities=(root,),
                    )
                except Exception:
                    return TreeDispatchReceipt(
                        dispatched=False,
                        signal_name="SIGKILL",
                        target_identities=(),
                    )

    def kill_by_identity(
        self,
        identity: ProcessBirthIdentity,
    ) -> TreeDispatchReceipt:
        """Kill one identity-verified root PID without claiming a tree reap.

        A persisted process birth identity cannot reconstruct the live Job
        Object or process-group handle required by ``kill_tree``. This method
        is therefore only a residual-cleanup safety net for the single root
        process; Job Object ``KILL_ON_JOB_CLOSE`` remains the normal Windows
        orphan-containment mechanism.
        """

        verified, _ = verify_process_identity(identity.pid, identity)
        if not verified:
            return TreeDispatchReceipt(
                dispatched=False,
                signal_name="KILL_BY_IDENTITY",
                target_identities=(),
            )

        if sys.platform == "win32":
            try:
                kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                # PROCESS_TERMINATE = 0x0001
                h_proc = kernel32.OpenProcess(0x0001, False, identity.pid)
                if h_proc:
                    try:
                        if kernel32.TerminateProcess(h_proc, 1):
                            return TreeDispatchReceipt(
                                dispatched=True,
                                signal_name="TerminateProcess",
                                target_identities=(identity,),
                            )
                    finally:
                        kernel32.CloseHandle(h_proc)
            except Exception:
                pass
            return TreeDispatchReceipt(
                dispatched=False,
                signal_name="KILL_BY_IDENTITY",
                target_identities=(),
            )

        try:
            os.kill(identity.pid, signal.SIGKILL)
            return TreeDispatchReceipt(
                dispatched=True,
                signal_name="SIGKILL",
                target_identities=(identity,),
            )
        except Exception:
            return TreeDispatchReceipt(
                dispatched=False,
                signal_name="SIGKILL",
                target_identities=(),
            )

    def observe_tree(
        self,
        tree: TreeHandle,  # pyright: ignore[reportUnknownParameterType]
    ) -> tuple[TreeProcessObservation, ...]:
        """Observe state of all processes in the managed process tree.

        Performs atomic identity verification against ProcessBirthIdentity.
        If creation time is mismatched, reports IDENTITY_UNCERTAIN.
        """
        root = tree.root_identity  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        verified, actual_creation = verify_process_identity(root.pid, root)  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
        if not verified:
            return (
                TreeProcessObservation(
                    identity=root,  # pyright: ignore[reportUnknownArgumentType]
                    state=ObservationState.IDENTITY_UNCERTAIN,
                    observed_creation_time=actual_creation,
                ),
            )

        observations: list[TreeProcessObservation] = []

        if sys.platform == "win32":
            job_handle = getattr(tree, "win_job_handle", None)  # pyright: ignore[reportUnknownArgumentType]
            if job_handle is not None:
                try:
                    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                    buf = JOBOBJECT_BASIC_PROCESS_ID_LIST()
                    res = kernel32.QueryInformationJobObject(
                        job_handle,
                        8,  # JobObjectBasicProcessIdList
                        ctypes.byref(buf),
                        ctypes.sizeof(buf),
                        None,
                    )
                    if res and buf.NumberOfProcessIdsInList > 0:
                        for i in range(buf.NumberOfProcessIdsInList):
                            pid = int(buf.ProcessIdList[i])
                            if pid == root.pid:  # pyright: ignore[reportUnknownMemberType]
                                is_active = _is_win_process_active(pid)
                                state = (
                                    ObservationState.RUNNING
                                    if is_active
                                    else ObservationState.TERMINATED
                                )
                                observations.append(
                                    TreeProcessObservation(
                                        identity=root,  # pyright: ignore[reportUnknownArgumentType]
                                        state=state,
                                        observed_creation_time=actual_creation,
                                    )
                                )
                            else:
                                child_identity = ProcessBirthIdentity(
                                    pid=pid,
                                    process_creation_time=_get_process_creation_time_ms(pid),
                                )
                                is_active = _is_win_process_active(pid)
                                state = (
                                    ObservationState.RUNNING
                                    if is_active
                                    else ObservationState.TERMINATED
                                )
                                observations.append(
                                    TreeProcessObservation(
                                        identity=child_identity,
                                        state=state,
                                        observed_creation_time=child_identity.process_creation_time,
                                    )
                                )
                        return tuple(observations)
                except Exception:
                    pass

            # Fallback for root process observation
            proc = getattr(tree, "process", None)  # pyright: ignore[reportUnknownArgumentType]
            if proc is not None:
                is_active = proc.poll() is None
            else:
                is_active = _is_win_process_active(root.pid)  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]

            state = (
                ObservationState.RUNNING
                if is_active
                else ObservationState.TERMINATED
            )
            return (
                TreeProcessObservation(
                    identity=root,  # pyright: ignore[reportUnknownArgumentType]
                    state=state,
                    observed_creation_time=actual_creation,
                ),
            )
        else:
            proc = getattr(tree, "process", None)
            if proc is not None:
                is_active = proc.poll() is None
            else:
                is_active = _is_posix_process_active(root.pid)

            state = (
                ObservationState.RUNNING
                if is_active
                else ObservationState.TERMINATED
            )
            return (
                TreeProcessObservation(
                    identity=root,
                    state=state,
                    observed_creation_time=actual_creation,
                ),
            )
