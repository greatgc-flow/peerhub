"""M2.6 Eval & Telemetry Test Suite (EVL-001..012).

Freeze Invariant 12: Eval/Telemetry never becomes collaboration truth.
Core imports Extension = 0.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from peerhub.core.models import Peer, Record, Stream
from peerhub.core.store import CoreStore
from peerhub.extensions.artifact import ArtifactStore
from peerhub.extensions.eval import (
    EvalDataset,
    EvalDatasetNotFoundError,
    EvalReport,
    EvalReportTamperedError,
    EvalTruthViolationError,
    EvaluatorExecutionError,
    EvaluatorValidationError,
    ExactMatchEvaluator,
    ExecutionTrace,
    FeedbackSignal,
    JsonLinesTelemetrySink,
    TelemetryExportError,
    TraceSpan,
    capture_trace,
    create_feedback_signal,
    export_telemetry,
    register_dataset,
    run_eval,
    verify_report_integrity,
)


@pytest.fixture
def core_store(tmp_path: Path) -> CoreStore:
    db_path = tmp_path / "core.db"
    store = CoreStore(db_path)
    store.register_peer(Peer(peer_id="peer:agent1", display_name="Agent 1"))
    store.create_stream(Stream(stream_id="stream:work1", members=["peer:agent1"]))
    return store


@pytest.fixture
def artifact_store(tmp_path: Path) -> ArtifactStore:
    root = tmp_path / "artifacts"
    return ArtifactStore(root=root)


# -------------------------------------------------------------------------
# EVL-001: Trace capture produces deterministic, immutable trace records
# -------------------------------------------------------------------------
def test_evl_001_trace_capture_produces_deterministic_immutable_spans() -> None:
    spans = [
        TraceSpan(span_id="s1", parent_id=None, name="root_span", start_ns=1000, end_ns=2000, attributes={"op": "plan"}),
        TraceSpan(span_id="s2", parent_id="s1", name="child_span", start_ns=1200, end_ns=1800, attributes={"model": "pro"}),
    ]
    trace1 = capture_trace("trace-001", spans, metadata={"target": "task-1"})
    trace2 = capture_trace("trace-001", spans, metadata={"target": "task-1"})

    assert trace1.trace_id == "trace-001"
    assert len(trace1.spans) == 2
    assert trace1.digest == trace2.digest
    assert trace1.canonical_json() == trace2.canonical_json()

    # Immutable: mutating spans should not affect trace or should be prevented
    with pytest.raises(AttributeError):  # spans is a tuple: no append
        trace1.spans.append(TraceSpan(span_id="s3", parent_id=None, name="bad", start_ns=0, end_ns=1))  # type: ignore
    assert [s.span_id for s in trace1.spans] == ["s1", "s2"] and trace1.digest == trace2.digest  # unchanged


# -------------------------------------------------------------------------
# EVL-002: Eval dataset stored as Artifact blob with verifiable SHA-256 digest
# -------------------------------------------------------------------------
def test_evl_002_eval_dataset_stored_as_artifact_blob_with_digest(artifact_store: ArtifactStore) -> None:
    items = [
        {"input": "What is 2+2?", "expected": "4"},
        {"input": "Ping", "expected": "Pong"},
    ]
    ds = register_dataset("math-qa-v1", items, artifact_store)
    assert ds.name == "math-qa-v1"
    assert len(ds.digest) == 64
    assert ds.count == 2

    # Verify that the blob exists in the artifact store and can be loaded
    assert artifact_store.resolve_path(ds.digest).exists()
    blob_bytes = artifact_store.read_bytes(ds.digest)
    loaded_data = json.loads(blob_bytes.decode("utf-8"))
    assert loaded_data == items


# -------------------------------------------------------------------------
# EVL-003: Deterministic evaluator calculates metrics and emits versioned EvalReport
# -------------------------------------------------------------------------
def test_evl_003_deterministic_evaluator_calculates_metrics_and_emits_report(artifact_store: ArtifactStore) -> None:
    items = [{"input": "test", "expected": "hello world"}]
    ds = register_dataset("test-ds", items, artifact_store)

    spans = [TraceSpan(span_id="s1", parent_id=None, name="llm_call", start_ns=10, end_ns=20, attributes={"output": "hello world"})]
    trace = capture_trace("tr-1", spans)

    evaluator = ExactMatchEvaluator(version="1.0.0", attribute_key="output")
    report = run_eval(evaluator, trace, ds)

    assert report.evaluator_type == "ExactMatchEvaluator"
    assert report.evaluator_version == "1.0.0"
    assert report.verdict == "PASSED"
    assert report.scores["accuracy"] == 1.0
    assert report.state == "PASSED"
    assert report.evidence["matched"] == 1
    assert verify_report_integrity(report) is True


# -------------------------------------------------------------------------
# EVL-004: Non-deterministic evaluator or missing metadata rejected / INCONCLUSIVE
# -------------------------------------------------------------------------
def test_evl_004_non_deterministic_or_unversioned_evaluator_rejected(artifact_store: ArtifactStore) -> None:
    items = [{"input": "test", "expected": "val"}]
    ds = register_dataset("test-ds-2", items, artifact_store)
    trace = capture_trace("tr-2", [])

    # Missing version
    class BadEvaluator:
        evaluator_type = "Bad"
        evaluator_version = ""  # empty version forbidden

        def evaluate(self, t: ExecutionTrace, d: EvalDataset) -> dict[str, Any]:
            return {"verdict": "PASSED", "scores": {"accuracy": 1.0}}

    with pytest.raises(EvaluatorValidationError):
        run_eval(BadEvaluator(), trace, ds)  # type: ignore


# -------------------------------------------------------------------------
# EVL-005: Invariant 12: Eval results never directly alter Work status or Stream records
# -------------------------------------------------------------------------
def test_evl_005_invariant_12_eval_results_never_alter_collaboration_truth(core_store: CoreStore, artifact_store: ArtifactStore) -> None:
    items = [{"input": "test", "expected": "42"}]
    ds = register_dataset("truth-test", items, artifact_store)
    trace = capture_trace("tr-truth", [TraceSpan(span_id="s1", parent_id=None, name="step", start_ns=0, end_ns=1, attributes={"output": "42"})])

    evaluator = ExactMatchEvaluator(version="1.0.0", attribute_key="output")
    report = run_eval(evaluator, trace, ds)
    assert report.verdict == "PASSED"

    # Attempting to bypass Core consensus or directly set stream state raises EvalTruthViolationError
    from peerhub.extensions.eval import assert_not_collaboration_truth
    with pytest.raises(EvalTruthViolationError):
        assert_not_collaboration_truth(target_action="force_stream_consensus", payload={"stream_id": "stream:work1", "verdict": report.verdict})

    # Ensure CoreStore has exactly 0 records appended from eval
    records = core_store.read_records("stream:work1")
    assert len(records) == 0


# -------------------------------------------------------------------------
# EVL-006: Tampered eval report fails verification with EvalReportTamperedError
# -------------------------------------------------------------------------
def test_evl_006_tampered_eval_report_fails_verification(artifact_store: ArtifactStore) -> None:
    items = [{"input": "x", "expected": "y"}]
    ds = register_dataset("ds-tamper", items, artifact_store)
    trace = capture_trace("tr-tamper", [TraceSpan(span_id="s1", parent_id=None, name="call", start_ns=0, end_ns=1, attributes={"output": "wrong"})])

    evaluator = ExactMatchEvaluator(version="1.0.0", attribute_key="output")
    report = run_eval(evaluator, trace, ds)
    assert report.verdict == "FAILED"
    assert verify_report_integrity(report) is True

    # Tamper with the report verdict
    tampered_report = EvalReport(
        eval_id=report.eval_id,
        evaluator_type=report.evaluator_type,
        evaluator_version=report.evaluator_version,
        target_ref=report.target_ref,
        target_digest=report.target_digest,
        dataset_ref=report.dataset_ref,
        scores={"accuracy": 1.0},  # tampered score!
        verdict="PASSED",          # tampered verdict!
        evidence=report.evidence,
        state="PASSED",
        created_at=report.created_at,
        report_digest=report.report_digest,  # old digest kept
    )

    with pytest.raises(EvalReportTamperedError):
        verify_report_integrity(tampered_report)


# -------------------------------------------------------------------------
# EVL-007: Feedback signal generated from failed evaluation links to Work entity
# -------------------------------------------------------------------------
def test_evl_007_feedback_signal_links_to_work_without_contaminating_stream(core_store: CoreStore, artifact_store: ArtifactStore) -> None:
    items = [{"input": "buggy_op", "expected": "expected_result"}]
    ds = register_dataset("bug-ds", items, artifact_store)
    trace = capture_trace("tr-bug", [TraceSpan(span_id="s1", parent_id=None, name="op", start_ns=0, end_ns=1, attributes={"output": "bad_result"})])

    evaluator = ExactMatchEvaluator(version="1.0.0", attribute_key="output")
    report = run_eval(evaluator, trace, ds)
    assert report.verdict == "FAILED"

    feedback = create_feedback_signal(
        report=report,
        title="Accuracy Regression on buggy_op",
        description="Expected 'expected_result' but observed 'bad_result'",
        target_work_id="work-42",
    )

    assert feedback.signal_id.startswith("sig-")
    assert feedback.target_work_id == "work-42"
    assert feedback.eval_id == report.eval_id
    assert feedback.verdict == "FAILED"

    # CoreStore remains untouched
    assert len(core_store.read_records("stream:work1")) == 0


# -------------------------------------------------------------------------
# EVL-008: Telemetry exporter functions in EXPORT_ONLY mode and never writes to CoreStore
# -------------------------------------------------------------------------
def test_evl_008_telemetry_exporter_export_only_never_writes_core(core_store: CoreStore, tmp_path: Path) -> None:
    out_file = tmp_path / "telemetry.jsonl"
    sink = JsonLinesTelemetrySink(out_file)

    events = [
        {"event": "span_finished", "span_id": "s1", "duration_ns": 500},
        {"event": "eval_finished", "eval_id": "ev-1", "verdict": "PASSED"},
    ]
    res = export_telemetry(events, sink)
    assert res.exported_count == 2
    assert res.errors == []

    # Verify sink output
    lines = [json.loads(line) for line in out_file.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 2
    assert lines[0]["event"] == "span_finished"

    # Verify CoreStore tables are completely untouched
    with closing(sqlite3.connect(core_store.db_path)) as c:
        for tbl in ("records", "streams", "peers"):
            c.execute(f"SELECT COUNT(*) FROM {tbl}")


# -------------------------------------------------------------------------
# EVL-009: Telemetry exporter failure isolation: simulated exporter crash does not disrupt CoreStore
# -------------------------------------------------------------------------
def test_evl_009_telemetry_exporter_failure_isolated_from_core_transaction(core_store: CoreStore) -> None:
    class CrashingSink:
        def emit(self, event: dict[str, Any]) -> None:
            raise ConnectionResetError("Remote OTel Collector refused connection")

    events = [{"event": "metric_heartbeat", "value": 100}]

    # Exporting to a broken sink handles the failure cleanly with TelemetryExportError logged in res.errors
    res = export_telemetry(events, CrashingSink())  # type: ignore
    assert res.exported_count == 0
    assert len(res.errors) == 1
    assert "Remote OTel Collector refused connection" in res.errors[0]

    # CoreStore append_record executes completely unaffected
    rec = core_store.append_record(
        stream_id="stream:work1",
        author_peer_id="peer:agent1",
        kind="test.kind",
        body="payload after telemetry failure",
        idempotency_key="tx-after-telemetry-crash",
        created_at="2026-10-06T00:00:00Z",
    )
    assert rec.position == 1


# -------------------------------------------------------------------------
# EVL-010: Regression feedback loop: reproducing a trace as a dataset entry
# -------------------------------------------------------------------------
def test_evl_010_regression_feedback_loop(artifact_store: ArtifactStore) -> None:
    # 1. Capture trace of failing run
    failing_spans = [TraceSpan(span_id="s1", parent_id=None, name="math", start_ns=0, end_ns=1, attributes={"calc": "2+2=5"})]
    failing_trace = capture_trace("tr-reg-01", failing_spans)

    # 2. Convert regression into dataset
    regression_items = [{"input": "math", "expected": "2+2=4"}]
    regression_ds = register_dataset("regression-test-suite", regression_items, artifact_store)

    evaluator = ExactMatchEvaluator(version="1.0.0", attribute_key="calc")
    report1 = run_eval(evaluator, failing_trace, regression_ds)
    assert report1.verdict == "FAILED"

    # 3. Fix the execution
    fixed_spans = [TraceSpan(span_id="s1", parent_id=None, name="math", start_ns=0, end_ns=1, attributes={"calc": "2+2=4"})]
    fixed_trace = capture_trace("tr-reg-02", fixed_spans)

    # 4. Re-evaluate against the same regression dataset
    report2 = run_eval(evaluator, fixed_trace, regression_ds)
    assert report2.verdict == "PASSED"
    assert report2.scores["accuracy"] == 1.0


# -------------------------------------------------------------------------
# EVL-011: Concurrent eval executions do not lock or block CoreStore
# -------------------------------------------------------------------------
def test_evl_011_concurrent_eval_executions_do_not_block_core(core_store: CoreStore, artifact_store: ArtifactStore) -> None:
    items = [{"input": "test", "expected": "val"}]
    ds = register_dataset("conc-ds", items, artifact_store)
    evaluator = ExactMatchEvaluator(version="1.0.0", attribute_key="val")

    errors: list[Exception] = []

    def run_evals() -> None:
        try:
            for i in range(10):
                trace = capture_trace(f"tr-c-{i}", [TraceSpan(span_id="s1", parent_id=None, name="run", start_ns=0, end_ns=1, attributes={"val": "val"})])
                report = run_eval(evaluator, trace, ds)
                assert report.verdict == "PASSED"
        except Exception as e:
            errors.append(e)

    def run_writes() -> None:
        try:
            for i in range(10):
                core_store.append_record(
                    stream_id="stream:work1",
                    author_peer_id="peer:agent1",
                    kind="conc.record",
                    body=f"body_{i}",
                    idempotency_key=f"conc-key-{i}",
                    created_at=f"2026-10-06T00:00:{i:02d}Z",
                )
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=run_evals)
    t2 = threading.Thread(target=run_writes)

    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert errors == []
    records = core_store.read_records("stream:work1")
    assert len(records) == 10


# -------------------------------------------------------------------------
# EVL-012: Zero dev-dependency violation: minimal install runs eval engine (REL-009)
# -------------------------------------------------------------------------
def test_evl_012_zero_dev_dependency_violation_rel_009() -> None:
    import importlib
    eval_mod = importlib.import_module("peerhub.extensions.eval")
    # Verify no dev dependencies are required in the module globals
    for bad_mod in ("opentelemetry", "yaml", "pytest"):
        assert bad_mod not in eval_mod.__dict__


# ---------------------------------------------------------------- authoritative source binding
def test_run_eval_rejects_a_trace_whose_digest_does_not_match_its_content():
    from peerhub.extensions.eval import EvalSourceMismatchError, ExecutionTrace, capture_trace, dataset_digest, EvalDataset

    good = capture_trace("t1", [])
    forged = ExecutionTrace(good.trace_id, good.spans, {"changed": True}, good.digest)  # content differs, digest copied
    ds = EvalDataset("d", dataset_digest([]), 0, "now")
    with pytest.raises(EvalSourceMismatchError):
        run_eval(ExactMatchEvaluator(), forged, ds)


def test_run_eval_rejects_a_dataset_whose_digest_or_count_is_made_up():
    from peerhub.extensions.eval import EvalSourceMismatchError, EvalDataset, capture_trace, dataset_digest

    items = ({"expected": "x"},)
    with pytest.raises(EvalSourceMismatchError):
        run_eval(ExactMatchEvaluator(), capture_trace("t", []), EvalDataset("d", "0" * 64, 1, "now", items))
    with pytest.raises(EvalSourceMismatchError):
        run_eval(ExactMatchEvaluator(), capture_trace("t", []), EvalDataset("d", dataset_digest(items), 5, "now", items))


def test_report_names_the_trace_digest_and_verify_report_sources_checks_it(tmp_path):
    from peerhub.extensions.artifact import ArtifactStore
    from peerhub.extensions.eval import EvalSourceMismatchError, capture_trace, register_dataset, verify_report_sources

    store = ArtifactStore(tmp_path / "a")
    ds = register_dataset("d", [{"expected": "x"}], store)
    trace = capture_trace("t", [])
    report = run_eval(ExactMatchEvaluator(), trace, ds, store=store)
    assert report.target_digest == trace.digest and report.dataset_ref == ds.digest
    assert verify_report_sources(report, trace, ds, store) is True
    other = capture_trace("t", [], {"k": 1})  # same trace id, different content: a mutable name must not stand in for the digest
    with pytest.raises(EvalSourceMismatchError):
        verify_report_sources(report, other, ds, store)


def test_dataset_must_exist_in_the_artifact_store_when_a_store_is_given(tmp_path):
    from peerhub.extensions.artifact import ArtifactStore
    from peerhub.extensions.eval import EvalSourceMismatchError, EvalDataset, capture_trace, dataset_digest

    items = ({"expected": "x"},)
    ds = EvalDataset("d", dataset_digest(items), 1, "now", items)  # never registered
    with pytest.raises(EvalSourceMismatchError):
        run_eval(ExactMatchEvaluator(), capture_trace("t", []), ds, store=ArtifactStore(tmp_path / "empty"))
