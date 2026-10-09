# Orchestration Contract (M3.4)

## 1. Authority Separation & Core Invariants
- **Bounded Planner / Executor:**
  - Orchestration is a bounded executor of an explicit, accepted plan (`Plan`).
  - Strict boundary: Planner generates a candidate `Plan`; Executor executes an accepted `Plan`.
  - The plan can be serialized and preserved as an `Artifact` or `Record`.
  - Orchestrator's authority is never source truth: authority resides strictly in `Work` / `Record` / `Artifact` (CoreStore / ArtifactStore). Orchestrator holds no private hidden truth.
- **Core Invariant 9: `Orchestration always bounded`:**
  - Every plan execution must enforce strict bounds:
    - `max_depth`: Maximum depth of hierarchical sub-plans or steps.
    - `max_steps`: Maximum total steps executed in a plan run.
    - `max_fanout`: Maximum concurrent or parallel steps allowed.
    - `max_attempts`: Maximum retry attempts per step.
    - `timeout_seconds`: Hard deadline per step and per overall execution.
    - `budget`: Optional resource / cost limit.
  - Exceeding any bound terminates execution immediately with `PlanBoundExceededError`.
- **Core Invariant 10: `MAY_HAVE_STARTED no blind replay`:**
  - Follows M1 Execution Certainty semantics.
  - If a step execution results in uncertain outcome (`MAY_HAVE_STARTED` or ambiguous network/process crash), the executor MUST NOT blindly replay without explicit idempotency verification or compensation.
  - Blind retry on ambiguous step state raises `AmbiguousExecutionReplayForbiddenError`.
- **Crash Recovery & Step Lifecycle:**
  - Step execution status: `PENDING` -> `RUNNING` -> `COMPLETED` | `FAILED` | `MAY_HAVE_STARTED`.
  - Re-hydrating an unfinished plan from recorded records enables deterministic resume or bounded safe abort.
- **Zero Dev-Dependency Violation (REL-009):**
  - Pure Python standard library implementation.

## 2. Public Interfaces & Protocols
The Orchestration module (`peerhub.m3.orchestration`) provides:
- `ExecutionCertainty`: `DEFINITELY_NOT_STARTED`, `MAY_HAVE_STARTED`, `DEFINITELY_COMPLETED`, `DEFINITELY_FAILED`
- `PlanStep`:
  - `step_id: str`
  - `action_type: str`
  - `payload: dict[str, Any]`
  - `idempotent: bool`
  - `depends_on: list[str]`
  - `max_attempts: int`
- `PlanBounds`:
  - `max_depth: int = 5`
  - `max_steps: int = 50`
  - `max_fanout: int = 10`
  - `max_attempts: int = 3`
  - `timeout_seconds: float = 300.0`
- `Plan`:
  - `plan_id: str`
  - `title: str`
  - `steps: list[PlanStep]`
  - `bounds: PlanBounds`
  - `created_at: str`
- `StepExecutionResult`:
  - `step_id: str`
  - `attempt: int`
  - `status: str` ("COMPLETED" | "FAILED" | "MAY_HAVE_STARTED")
  - `output: dict[str, Any] | None`
  - `error: str | None`
- `PlanExecutionSummary`:
  - `plan_id: str`
  - `status: str` ("COMPLETED" | "FAILED" | "BOUNDS_EXCEEDED" | "CANCELLED")
  - `steps_executed: int`
  - `step_results: dict[str, StepExecutionResult]`
  - `elapsed_seconds: float`
- `PlanExecutor`:
  - `execute_plan(plan: Plan, step_runner: Callable[[PlanStep, int], StepExecutionResult]) -> PlanExecutionSummary`
  - `resume_plan(plan: Plan, recorded_results: dict[str, StepExecutionResult], step_runner: Callable[[PlanStep, int], StepExecutionResult]) -> PlanExecutionSummary`

## 3. Plan & Step Lifecycle State Machine
```text
[Plan Lifecycle]
      ┌───────────┐
      │  PENDING  │
      └─────┬─────┘
            ▼
      ┌───────────┐
      │  RUNNING  ├────────────────────────┐
      └─┬───────┬─┘                        │
        │       │                          │
        ▼       ▼                          ▼
  ┌──────────┐ ┌────────┐ ┌─────────────────┴───────────────┐
  │COMPLETED │ │ FAILED │ │ BOUNDS_EXCEEDED / MAY_HAVE_STARTED│
  └──────────┘ └────────┘ └─────────────────────────────────┘

[Step Lifecycle]
      ┌───────────┐
      │  PENDING  │
      └─────┬─────┘
            ▼
      ┌───────────┐
      │  RUNNING  ├───────────────────────┐
      └─┬───────┬─┘                       │
        │       │                         │
        ▼       ▼                         ▼
  ┌──────────┐ ┌────────┐      ┌──────────────────┐
  │COMPLETED │ │ FAILED │      │ MAY_HAVE_STARTED │
  └──────────┘ └───┬────┘      └────────┬─────────┘
                   │ (retry if bounded)  │ (blind retry forbidden)
                   └───────────►─────────┘
```

---

## Closure update (2026-10-08)

Written after the M2/M3 closure work (`docs/implementation/M2_M3_CLOSURE_2026-10-08.md`) and checked against the code and tests. Where it conflicts with the text above, **this section wins**. The state-machine and exception JSON catalogs carry the same update.

- `PlanBounds` supports a `cost_budget`. `PlanStep` execution payloads now contain a `cost` result. Cost is aggregated across attempts and executor restarts. Unreported costs evaluate as "unknown" (not zero), and if a budget is enforced, missing costs fail the execution.
- `PlanStep` utilizes deterministic `when` and `stop_if` predicates. Skipping an unfulfilled `when` step automatically skips all of its dependents. `stop_if` cleanly halts the remaining plan.
- Parallel execution is supported via an opt-in thread pool bounded by `max_fanout`. Durable journal appends remain strictly serialized even in parallel execution.
- `max_depth` limits are statically verified and calculated against the actual dependency chain.

**Superseded or missing in the original text:**
- The old `PlanBounds` lacked `cost_budget`.
- The old `PlanStep` object lacked `when`, `stop_if`, and `cost` tracking payloads.
- The `SKIPPED` state was entirely missing.
