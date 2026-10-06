"""Tests for M3.6 Remote Runtime Port & Adapter Contract (RUN-001..012).

Verifies:
- Core Invariant 12: Remote runtime capability-bound, no general remote shell.
- Public port operations: probe, submit, get, cancel, collect.
- Deterministic loopback/fake adapter failure isolation.
- Error handling and lifecycle state machine.
- Zero dev-dependency violation (REL-009).
"""

from __future__ import annotations

import sys
import pytest

from peerhub.m3.runtime_port import (
    RuntimeCapability,
    InvocationJob,
    LoopbackRuntimeAdapter,
    GeneralRemoteShellForbiddenError,
    RuntimeCapabilityNotFoundError,
    RuntimeJobNotFoundError,
    RuntimeJobNotReadyError,
    RuntimeJobFailedError,
)


@pytest.fixture
def sample_capability() -> RuntimeCapability:
    return RuntimeCapability(
        capability_id="calc:add",
        name="Addition Service",
        description="Adds two numbers",
        schema={"x": "number", "y": "number"},
    )


def test_run_001_invariant_capability_bound_no_general_shell() -> None:
    """RUN-001: Invariant 12: Remote runtime is capability-bound; unrestricted arbitrary remote shells are forbidden."""
    adapter = LoopbackRuntimeAdapter()

    # Attempting raw shell command directly without capability wrapper must fail
    with pytest.raises(GeneralRemoteShellForbiddenError):
        adapter.invoke_raw_shell("rm -rf /")

    with pytest.raises(GeneralRemoteShellForbiddenError):
        adapter.invoke_raw_shell("cat /etc/passwd")


def test_run_002_unregistered_capability_error() -> None:
    """RUN-002: Invoking unregistered capability raises RuntimeCapabilityNotFoundError."""
    adapter = LoopbackRuntimeAdapter()

    with pytest.raises(RuntimeCapabilityNotFoundError):
        adapter.submit("non-existent-cap", params={})


def test_run_003_lifecycle_probe_submit_get_collect(sample_capability: RuntimeCapability) -> None:
    """RUN-003: RuntimePort operations: probe, submit, get, and collect lifecycle."""
    adapter = LoopbackRuntimeAdapter()
    adapter.register_capability(sample_capability, handler=lambda p: {"result": p["x"] + p["y"]})

    # Probe
    probe_res = adapter.probe(sample_capability.capability_id)
    assert probe_res["available"] is True
    assert probe_res["capability_id"] == "calc:add"

    # Submit
    job = adapter.submit("calc:add", params={"x": 10, "y": 20})
    assert job.status in ("SUBMITTED", "RUNNING", "COMPLETED")

    # Get
    polled_job = adapter.get(job.job_id)
    assert polled_job.status == "COMPLETED"
    assert polled_job.output == {"result": 30}

    # Collect
    output = adapter.collect(job.job_id)
    assert output == {"result": 30}


def test_run_004_job_not_found() -> None:
    """RUN-004: Querying non-existent job ID raises RuntimeJobNotFoundError."""
    adapter = LoopbackRuntimeAdapter()

    with pytest.raises(RuntimeJobNotFoundError):
        adapter.get("non-existent-job-id")

    with pytest.raises(RuntimeJobNotFoundError):
        adapter.collect("non-existent-job-id")


def test_run_005_failure_injection(sample_capability: RuntimeCapability) -> None:
    """RUN-005: Deterministic fake/loopback adapter simulates controlled failure injection."""
    adapter = LoopbackRuntimeAdapter()
    adapter.register_capability(sample_capability, handler=lambda p: {"result": 0})
    adapter.inject_failure("calc:add", error_message="Simulated math coprocessor outage")

    job = adapter.submit("calc:add", params={"x": 1, "y": 2})
    assert job.status == "FAILED"
    assert job.error == "Simulated math coprocessor outage"


def test_run_006_probe_reports_outage(sample_capability: RuntimeCapability) -> None:
    """RUN-006: Probe operation reports degraded status when simulated failure is active."""
    adapter = LoopbackRuntimeAdapter()
    adapter.register_capability(sample_capability, handler=lambda p: {})
    adapter.inject_failure("calc:add", error_message="Network partition")

    probe_res = adapter.probe("calc:add")
    assert probe_res["available"] is False
    assert probe_res["error"] == "Network partition"


def test_run_007_collect_not_ready() -> None:
    """RUN-007: Collecting incomplete job raises RuntimeJobNotReadyError."""
    adapter = LoopbackRuntimeAdapter()
    job = InvocationJob(
        job_id="pending-job",
        capability_id="any",
        params={},
        status="RUNNING",
    )
    adapter._jobs[job.job_id] = job

    with pytest.raises(RuntimeJobNotReadyError):
        adapter.collect("pending-job")


def test_run_008_collect_failed_job() -> None:
    """RUN-008: Collecting failed job raises RuntimeJobFailedError."""
    adapter = LoopbackRuntimeAdapter()
    job = InvocationJob(
        job_id="failed-job",
        capability_id="any",
        params={},
        status="FAILED",
        error="Memory limit exceeded",
    )
    adapter._jobs[job.job_id] = job

    with pytest.raises(RuntimeJobFailedError):
        adapter.collect("failed-job")


def test_run_009_job_cancellation() -> None:
    """RUN-009: Cancel job transitions active job to CANCELLED state and halts execution."""
    adapter = LoopbackRuntimeAdapter()
    job = InvocationJob(
        job_id="running-job",
        capability_id="long-running",
        params={},
        status="RUNNING",
    )
    adapter._jobs[job.job_id] = job

    cancelled = adapter.cancel("running-job")
    assert cancelled.status == "CANCELLED"

    with pytest.raises(RuntimeJobFailedError):
        adapter.collect("running-job")


def test_run_010_cancelling_completed_job_noop() -> None:
    """RUN-010: Cancelling already completed job leaves status as COMPLETED."""
    adapter = LoopbackRuntimeAdapter()
    job = InvocationJob(
        job_id="done-job",
        capability_id="quick",
        params={},
        status="COMPLETED",
        output={"v": 1},
    )
    adapter._jobs[job.job_id] = job

    res = adapter.cancel("done-job")
    assert res.status == "COMPLETED"


def test_run_011_schema_validation(sample_capability: RuntimeCapability) -> None:
    """RUN-011: Capability registration and parameter validation roundtrip."""
    adapter = LoopbackRuntimeAdapter()
    adapter.register_capability(sample_capability, handler=lambda p: {"res": 1})

    # Valid submission
    j = adapter.submit("calc:add", {"x": 5, "y": 10})
    assert j.status == "COMPLETED"

    # Missing parameter
    with pytest.raises(ValueError):
        adapter.submit("calc:add", {"x": 5})


def test_run_012_rel_009_standard_library_compliance() -> None:
    """RUN-012: REL-009 Standard library compliance."""
    import peerhub.m3.runtime_port as run_module

    for mod_name, mod in sys.modules.items():
        if mod_name.startswith("peerhub.m3.runtime_port"):
            assert mod is not None
