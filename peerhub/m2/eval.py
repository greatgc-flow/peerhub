"""M2.6 Eval & Telemetry Engine.

Implements execution trace capture, artifact-backed evaluation datasets,
deterministic evaluation runner, tamper-verifiable evaluation reports,
advisory feedback signals, and export-only telemetry sinks.

Core Invariant 12: Eval/Telemetry never becomes collaboration truth.
Core imports Extension = 0.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, Sequence, cast


# -----------------------------------------------------------------------------
# Exceptions (EXC-040 .. EXC-045)
# -----------------------------------------------------------------------------
class EvalDatasetNotFoundError(Exception):
    """Referenced eval dataset or artifact digest does not exist (EXC-040)."""


class EvaluatorExecutionError(Exception):
    """Evaluator execution failed or crashed unexpectedly (EXC-041)."""


class EvalReportTamperedError(Exception):
    """Eval report contents do not match calculated verification digest (EXC-042)."""


class TelemetryExportError(Exception):
    """Telemetry exporter sink failed to write or export events (EXC-043)."""


class EvalTruthViolationError(Exception):
    """Attempted to use evaluation result or telemetry as collaboration truth (EXC-044)."""


class EvaluatorValidationError(Exception):
    """Evaluator missing required metadata or produces invalid score types (EXC-045)."""


# -----------------------------------------------------------------------------
# Canonical JSON serialization helper
# -----------------------------------------------------------------------------
def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _canonical_bytes(obj: Any) -> bytes:
    return _canonical_json(obj).encode("utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# -----------------------------------------------------------------------------
# Trace Capture
# -----------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class TraceSpan:
    span_id: str
    parent_id: str | None
    name: str
    start_ns: int
    end_ns: int
    attributes: dict[str, Any] = dataclasses.field(default_factory=lambda: cast(dict[str, Any], {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "start_ns": self.start_ns,
            "end_ns": self.end_ns,
            "attributes": self.attributes,
        }


@dataclasses.dataclass(frozen=True)
class ExecutionTrace:
    trace_id: str
    spans: tuple[TraceSpan, ...]
    metadata: dict[str, Any]
    digest: str

    def canonical_json(self) -> str:
        payload = {
            "trace_id": self.trace_id,
            "spans": [s.to_dict() for s in self.spans],
            "metadata": self.metadata,
        }
        return _canonical_json(payload)


def capture_trace(
    trace_id: str,
    spans: Sequence[TraceSpan],
    metadata: dict[str, Any] | None = None,
) -> ExecutionTrace:
    meta = metadata or {}
    spans_tuple = tuple(spans)
    payload = {
        "trace_id": trace_id,
        "spans": [s.to_dict() for s in spans_tuple],
        "metadata": meta,
    }
    digest = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    return ExecutionTrace(
        trace_id=trace_id,
        spans=spans_tuple,
        metadata=meta,
        digest=digest,
    )


# -----------------------------------------------------------------------------
# Evaluation Dataset (Backbone: ArtifactStore)
# -----------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class EvalDataset:
    name: str
    digest: str
    count: int
    created_at: str
    items: tuple[dict[str, Any], ...] = ()


def register_dataset(
    name: str,
    items: list[dict[str, Any]],
    store: Any,  # ArtifactStore duck-typed to prevent hard circular dependencies
) -> EvalDataset:
    content_bytes = _canonical_bytes(items)
    digest = hashlib.sha256(content_bytes).hexdigest()
    now = _now_iso()

    # Stage and commit into ArtifactStore
    staged = store.stage_bytes(content_bytes)
    store.commit_staged(staged)

    return EvalDataset(
        name=name,
        digest=digest,
        count=len(items),
        created_at=now,
        items=tuple(items),
    )


# -----------------------------------------------------------------------------
# Evaluator Protocol & Built-in Deterministic Evaluator
# -----------------------------------------------------------------------------
class Evaluator(Protocol):
    evaluator_type: str
    evaluator_version: str

    def evaluate(self, trace: ExecutionTrace, dataset: EvalDataset) -> dict[str, Any]:
        ...


class ExactMatchEvaluator:
    evaluator_type: str = "ExactMatchEvaluator"

    def __init__(self, version: str = "1.0.0", attribute_key: str = "output") -> None:
        self.evaluator_version = version
        self.attribute_key = attribute_key

    def evaluate(self, trace: ExecutionTrace, dataset: EvalDataset) -> dict[str, Any]:
        if not dataset.items or not trace.spans:
            return {
                "verdict": "FAILED",
                "scores": {"accuracy": 0.0},
                "evidence": {"matched": 0, "total": dataset.count},
            }

        matched = 0
        for item in dataset.items:
            expected = str(item.get("expected", ""))
            found = False
            for span in trace.spans:
                actual = str(span.attributes.get(self.attribute_key, ""))
                if actual == expected:
                    found = True
                    break
            if found:
                matched += 1

        total = len(dataset.items)
        accuracy = matched / max(total, 1)
        verdict = "PASSED" if accuracy >= 1.0 else "FAILED"
        return {
            "verdict": verdict,
            "scores": {"accuracy": accuracy},
            "evidence": {"matched": matched, "total": total},
        }


# -----------------------------------------------------------------------------
# Evaluation Report
# -----------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class EvalReport:
    eval_id: str
    evaluator_type: str
    evaluator_version: str
    target_ref: str
    dataset_ref: str
    scores: dict[str, float]
    verdict: str
    evidence: dict[str, Any]
    state: str
    created_at: str
    report_digest: str


def _calculate_report_digest(report_dict: dict[str, Any]) -> str:
    copy_dict = dict(report_dict)
    copy_dict.pop("report_digest", None)
    return hashlib.sha256(_canonical_bytes(copy_dict)).hexdigest()


def run_eval(
    evaluator: Any,
    trace: ExecutionTrace,
    dataset: EvalDataset,
) -> EvalReport:
    # 1. Validate Evaluator metadata
    etype = getattr(evaluator, "evaluator_type", None)
    eversion = getattr(evaluator, "evaluator_version", None)
    if not etype or not eversion or not isinstance(eversion, str) or not eversion.strip():
        raise EvaluatorValidationError("Evaluator must define non-empty evaluator_type and evaluator_version")

    # 2. Execute Evaluator
    try:
        res = evaluator.evaluate(trace, dataset)
    except Exception as e:
        raise EvaluatorExecutionError(f"Evaluator execution failed: {e}") from e

    verdict = res.get("verdict", "INCONCLUSIVE")
    if verdict not in ("PASSED", "FAILED", "INCONCLUSIVE"):
        raise EvaluatorValidationError(f"Invalid verdict: {verdict}")

    scores = {str(k): float(v) for k, v in res.get("scores", {}).items()}
    evidence = res.get("evidence", {})
    now = _now_iso()
    eval_id = f"ev-{hashlib.sha256(f'{trace.trace_id}:{dataset.digest}:{now}'.encode()).hexdigest()[:16]}"

    report_payload = {
        "eval_id": eval_id,
        "evaluator_type": etype,
        "evaluator_version": eversion,
        "target_ref": trace.trace_id,
        "dataset_ref": dataset.digest,
        "scores": scores,
        "verdict": verdict,
        "evidence": evidence,
        "state": verdict,
        "created_at": now,
    }
    report_digest = _calculate_report_digest(report_payload)

    return EvalReport(
        eval_id=eval_id,
        evaluator_type=etype,
        evaluator_version=eversion,
        target_ref=trace.trace_id,
        dataset_ref=dataset.digest,
        scores=scores,
        verdict=verdict,
        evidence=evidence,
        state=verdict,
        created_at=now,
        report_digest=report_digest,
    )


def verify_report_integrity(report: EvalReport) -> bool:
    report_dict = dataclasses.asdict(report)
    expected_digest = _calculate_report_digest(report_dict)
    if expected_digest != report.report_digest:
        raise EvalReportTamperedError(
            f"Eval report digest mismatch: expected {expected_digest}, found {report.report_digest}"
        )
    return True


# -----------------------------------------------------------------------------
# Collaboration Truth Guard (Invariant 12)
# -----------------------------------------------------------------------------
def assert_not_collaboration_truth(target_action: str, payload: dict[str, Any]) -> None:
    """Enforce Invariant 12: Eval/Telemetry never becomes collaboration truth.

    Blocks any attempt by automated eval or telemetry pipelines to inject stream
    records or force work transitions without peer consensus.
    """
    forbidden_actions = {
        "force_stream_consensus",
        "override_stream_membership",
        "bypass_work_revision",
        "eval_direct_commit",
    }
    if target_action in forbidden_actions or "force" in target_action:
        raise EvalTruthViolationError(
            f"Invariant 12 Violation: Action '{target_action}' attempts to turn eval results into collaboration truth."
        )


# -----------------------------------------------------------------------------
# Feedback Signal
# -----------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class FeedbackSignal:
    signal_id: str
    eval_id: str
    title: str
    description: str
    target_work_id: str | None
    verdict: str
    created_at: str


def create_feedback_signal(
    report: EvalReport,
    title: str,
    description: str,
    target_work_id: str | None = None,
) -> FeedbackSignal:
    now = _now_iso()
    signal_id = f"sig-{hashlib.sha256(f'{report.eval_id}:{title}:{now}'.encode()).hexdigest()[:12]}"
    return FeedbackSignal(
        signal_id=signal_id,
        eval_id=report.eval_id,
        title=title,
        description=description,
        target_work_id=target_work_id,
        verdict=report.verdict,
        created_at=now,
    )


# -----------------------------------------------------------------------------
# Telemetry Sinks & Exporters (EXPORT_ONLY, Failure-Isolated)
# -----------------------------------------------------------------------------
class TelemetrySink(Protocol):
    def emit(self, event: dict[str, Any]) -> None:
        ...


class JsonLinesTelemetrySink:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path

    def emit(self, event: dict[str, Any]) -> None:
        line = _canonical_json(event) + "\n"
        with open(self.file_path, "a", encoding="utf-8") as f:
            f.write(line)


@dataclasses.dataclass(frozen=True)
class ExportResult:
    exported_count: int
    errors: list[str]


def export_telemetry(
    events: Sequence[dict[str, Any]],
    sink: TelemetrySink,
) -> ExportResult:
    """Export telemetry events to an external sink in EXPORT_ONLY mode.

    Failure isolation: errors are caught, logged in ExportResult.errors,
    and NEVER bubble up to crash the caller's transaction or storage state.
    """
    exported = 0
    errors: list[str] = []
    for event in events:
        try:
            sink.emit(event)
            exported += 1
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")

    return ExportResult(
        exported_count=exported,
        errors=errors,
    )
