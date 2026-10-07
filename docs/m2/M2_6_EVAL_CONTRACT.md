# Eval & Telemetry Contract (M2.6)

## 1. Authority Separation & Core Invariants
- **Authority Separation (Invariant 12):**
  - **Execution Trace:** Structured, immutable execution record (spans, events, inputs, outputs, timestamps). Used purely for observation, diagnostics, and evaluation.
  - **Evaluation Dataset:** Curated test cases and gold-standard expectations stored authoritative as **Artifacts** in `ArtifactStore` with deterministic SHA-256 digests.
  - **Evaluation Report:** Derived analysis produced by an Evaluator against a Trace and Dataset. Contains `evaluator_type`, `evaluator_version`, `target_ref`, `dataset_ref`, `scores`, `verdict`, and `evidence`.
  - **Feedback Signal:** Advisory actionable signal linking an evaluation outcome to work items or regression test definitions.
  - **Telemetry Exporter:** Strictly `EXPORT_ONLY` adapter/exporter (e.g. OpenTelemetry or JSONL format).
- **Core Invariant 12:** `Eval/Telemetry never becomes collaboration truth`:
  - An evaluation report or score cannot unilaterally alter Work projection status, mutate stream membership, or bypass CoreStore authorization.
  - CoreStore transactions and state machines remain 100% functional and unblocked even if telemetry exporters fail or crash (Failure Isolation).

## 2. Public Interfaces & Protocols
The Eval & Telemetry engine (`peerhub.m2.eval`) provides typed interfaces:
- `capture_trace(trace_id: str, spans: list[TraceSpan], metadata: dict[str, Any] | None = None) -> ExecutionTrace`
- `register_dataset(name: str, items: list[dict[str, Any]], store: ArtifactStore) -> EvalDataset`
  Errors: `ArtifactCommitError`, `InvalidDatasetError`.
- `run_eval(evaluator: Evaluator, trace: ExecutionTrace, dataset: EvalDataset) -> EvalReport`
  Errors: `EvaluatorValidationError`, `EvaluatorExecutionError`.
- `verify_report_integrity(report: EvalReport) -> bool`
  Errors: `EvalReportTamperedError`.
- `create_feedback_signal(report: EvalReport, title: str, description: str) -> FeedbackSignal`
  Errors: `EvalTruthViolationError` (if attempted to bypass Core consensus).
- `export_telemetry(events: list[dict[str, Any]], sink: TelemetrySink) -> ExportResult`
  Errors: `TelemetryExportError` (isolated from core).

## 3. Evaluation State Machine
```text
           ┌──────────────────────┐
           │       PENDING        │
           └──────────┬───────────┘
                      │
                      ▼
           ┌──────────────────────┐
           │       RUNNING        │
           └──────┬───┬───┬───────┘
                  │   │   │
        ┌─────────┘   │   └─────────┐
        ▼             ▼             ▼
   ┌─────────┐   ┌─────────┐   ┌──────────────┐
   │ PASSED  │   │ FAILED  │   │ INCONCLUSIVE │
   └─────────┘   └─────────┘   └──────────────┘
```
- An evaluation run starts in `PENDING` and moves to `RUNNING`.
- From `RUNNING`, it concludes deterministically into `PASSED`, `FAILED`, or `INCONCLUSIVE`.
- Terminal states cannot transition back to `RUNNING`.

## 4. Failure & Dependency Isolation
- Exporters operate strictly out-of-band or with failure-catching wrappers; an export sink error never raises into CoreStore operations.
- Minimal installations run all trace capture and evaluation logic using standard library components; external observability frameworks (e.g. `opentelemetry`) are optional plugins.

---

## Closure update (2026-10-08)

Written after the M2/M3 closure work (`docs/implementation/M2_M3_CLOSURE_2026-10-08_KO.md`) and checked against the code and tests. Where it conflicts with the text above, **this section wins**. State-machine and exception JSON catalogs are updated separately.

Section 1: replace the execution-trace and report bullets:

```markdown
  - **Execution Trace:** Structured trace with frozen dataclass fields and a tuple of spans. Nested metadata, span attributes, and dataset items remain mutable; source verification detects content that no longer matches its recorded digest.
  - **Evaluation Report:** Derived analysis containing `evaluator_type`, `evaluator_version`, `target_ref`, `target_digest`, `dataset_ref`, `scores`, `verdict`, and `evidence`. `target_ref` names the trace; `target_digest` binds its content. Both source bindings are included in the report digest.
```

Section 2: replace `run_eval` and add `verify_report_sources`:

```markdown
- `run_eval(evaluator: Evaluator, trace: ExecutionTrace, dataset: EvalDataset, store=None) -> EvalReport`
  Recomputes the trace digest and dataset-items digest and checks dataset count before evaluator execution. When `store` is supplied, the dataset blob must exist, verify, and equal the canonical dataset bytes.
  Errors: `EvaluatorValidationError`, `EvaluatorExecutionError`, `EvalSourceMismatchError`.
- `verify_report_sources(report: EvalReport, trace: ExecutionTrace, dataset: EvalDataset, store=None) -> bool`
  Verifies report integrity, recomputes supplied source digests, optionally verifies the dataset artifact, and checks `target_ref`, `target_digest`, and `dataset_ref`.
  Errors: `EvalReportTamperedError`, `EvalSourceMismatchError`.
```

Replace section 3’s diagram and bullets:

```markdown
[Evaluation Procedure]
  SOURCE_CHECK -> EVALUATOR_EXECUTION -> SNAPSHOT_CHECK -> REPORT_SEALED
       |                  |                    |
       +------------------+--------------------+-> REJECTED
```

```markdown
- These are procedural phases, not persisted PENDING/RUNNING states or a public transition API. A successful report records `state` equal to its validated verdict: PASSED, FAILED, or INCONCLUSIVE.
- The evaluator receives deep private copies of the verified trace and dataset. Their content digests and dataset count are rechecked after evaluator execution, before a report is created.
- Mutating snapshot content without preserving its verified source identity raises `EvalSourceMismatchError`; the caller's nested objects remain unchanged by evaluator mutations.
- Report-integrity verification alone proves the report's internal digest. `verify_report_sources` additionally verifies its binding to the supplied sources.
```

**Wrong/obsolete:** deeply immutable input objects; source identity established by trace name alone; ArtifactStore verification being mandatory without supplying a store; persisted PENDING/RUNNING transitions; and a terminal-state transition guard. Source mismatches raise `EvalSourceMismatchError`, including missing/unverifiable dataset artifacts when a store is supplied.
