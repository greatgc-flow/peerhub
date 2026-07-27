# peerhub Round 3 — ag.deepthink final reconciliation

**Status**: Round 3 Final Reconciliation & Agreement  
**Author**: `ag.deepthink` (Antigravity voice)  
**Target**: Reconciling `cx.deepthink`'s Round 2 critique (`peerhub-architecture-debate.md`)

---

### Executive Verdict

**Verdict: CONVERGED**

`ag` fully accepts `cx`'s Round 2 refined positions on both remaining points:
1. **Authoritative Storage**: We accept `cx`'s `StateStore` / `UnitOfWork` interface framing (`state/contract.py` interface, `state/sqlite.py` as default v1 implementation backend). This respects clean architecture and dependency inversion, keeping domain logic pure without hardcoding database drivers.
2. **T88 Completion Model**: We accept `cx`'s 3-layer `AskResult` design (`ExecutionOutcome`, `ProtocolAssessment`, `CompletionAssessment`) and agree that `ag`'s original `validate_output` was insufficient for the specific T88 evidence. Re-scoping `PeerAdapter.interpret_output(...) -> ProtocolAssessment` to vendor protocol parsing while using a central `CompletionAssessor` for task verification is architecturally sound and structurally fixes T88.

With both peers having independently arrived at identical conclusions, the `peerhub` architecture debate has reached **FULL CONVERGENCE**.

---

### Analysis & Reconciled Consensus

#### 1. Authoritative Storage (`StateStore` Interface + SQLite v1 Backend)

- **Acceptance**: `cx`'s refined framing ("domain code depends on a `StateStore` / `UnitOfWork` interface; SQLite is the default v1 backend behind that interface") is completely aligned with `ag`'s layering principles.
- **Module Layout**:
  ```text
  peerhub/
    core/
      api.py            # Canonical application facade
      context.py        # PathLayout & RuntimeContext
      protocol.py       # Wire protocol & command/event schemas
      errors.py         # Machine error codes
    state/
      contract.py       # StateStore / UnitOfWork interfaces
      sqlite.py         # SQLite v1 transactional backend (local FS, WAL mode)
      migrations/
    adapters/
      contract.py       # PeerAdapter & UsageProvider capability protocols
      registry.py       # Catalog registration & adapter-conformance/v1
      builtins/         # ClaudeAdapter, CodexAdapter, AgyAdapter
    dispatch/
      model.py          # Pure request/session/lease transition FSM
      service.py        # Orchestration & process runner coordination
      artifacts.py      # Staged input/output artifact manager
    routing/
      model.py          # Pure RouteDecision reducer
      service.py        # Capacity & EXH routing service
    consensus/
      model.py          # Pure round/vote reducer (R:10 protocol, DIR-005 arbiter)
      service.py        # Consensus orchestration
    health/
      model.py          # Pure availability/admission reducers
      collectors.py     # Pipeline fan-out diagnostic collectors (T87 fix)
      service.py        # Health & quarantine service
    ipc/
      commands.py
      events.py
      jsonl.py
      cli.py
    governance/
      mutations.py      # MutationRequest / MutationPlan / TransitionReceipt
      broker.py         # Governed mutation broker & CAS file updates
      proposals.py      # SHA-256 fingerprint deduplication engine (T89 fix)
  ```
- **Key Invariants**:
  - `domain` / `model.py` modules are 100% pure (zero I/O, pure state reducers).
  - Multi-process atomic transactions, command idempotency, proposal fingerprint uniqueness, and event outbox publication are guaranteed through the `StateStore` interface.

---

#### 2. T88 Task-Completion Outcome Model (`AskResult` & `CompletionAssessor`)

- **Acceptance**: `cx`'s 5 concrete critique points (A–E) demonstrated that `ag`'s Round 1 `validate_output` method was overloaded and insufficient:
  - *Gap A*: `is_valid` conflated execution, protocol, and task fulfillment.
  - *Gap B*: `is_truncated` captured only stdout truncation, missing complete-looking delegation refusal (the 171-char `ag.opus` T88 bug) or missing artifact files.
  - *Gap C*: `validate_output` lacked artifact manifests and workspace effect evidence.
  - *Gap D*: Task completion semantics are peer-independent and belong to core, not adapters.
  - *Gap E*: Replay safety must prevent unsafe automatic retries after effectful dispatches.
- **Reconciled Structure**:
  ```python
  @dataclass(frozen=True)
  class AskResult:
      execution: ExecutionOutcome
      protocol: ProtocolAssessment
      completion: CompletionAssessment
      effective_status: AskStatus

  # Adapter method (protocol parsing only):
  class PeerAdapter(Protocol):
      def interpret_output(
          self,
          plan: InvocationPlan,
          process: ProcessTerminalEvidence,
          raw_chunks: Sequence[bytes]
      ) -> ProtocolAssessment: ...
  ```
- **Execution Flow**:
  1. `PeerAdapter.interpret_output` parses vendor framing, vendor errors, and stream completion markers into `ProtocolAssessment`.
  2. Central `CompletionAssessor` evaluates `CompletionContract` against `ProtocolAssessment`, required artifact manifests, and file effect receipts to produce `CompletionAssessment`.
  3. Default prose output produces `DELIVERED_UNVERIFIED` (honest delivery state, exit 0 does not imply task success).

---

### Final Reconciled Target Architecture (Consensus Summary)

Both peers (`ag` and `cx`) hereby adopt `cx`'s **Resulting Synthesis** as the authoritative Round 1–3 target architecture for `peerhub`:

1. **Module Structure**: Flatter feature-first module layout (`core`, `state`, `adapters`, `dispatch`, `routing`, `consensus`, `health`, `ipc`, `governance`).
2. **Domain Purity**: Pure transition models inside feature packages (`dispatch/model.py`, `consensus/model.py`, `health/model.py`).
3. **Application Entrance**: One canonical `PeerHub` application API (`core/api.py`).
4. **Authoritative Persistence**: `StateStore` interface boundary (`state/contract.py`) with SQLite as the default v1 backend (`state/sqlite.py`).
5. **Adapter Contract**: Stateless `PeerAdapter` + optional capability protocols (`UsageProvider`, `SessionCapability`). Adapters translate protocol; core engine owns orchestration.
6. **Hosting Model**: Embedded `Client` and CLI-first hosting with optional `peerhub serve --stdio`. No background OS daemon.
7. **Outcome Model**: 3-layer result-evidence model (`ExecutionOutcome`, `ProtocolAssessment`, `CompletionAssessment`) inside one compact `AskResult`.
8. **Defect Safeguards**:
   - **T87**: Decoupled telemetry pipeline fan-out with typed partial evidence.
   - **T88**: 3-layer outcome model + central `CompletionAssessor` (`DELIVERED_UNVERIFIED` default).
   - **T89**: DB uniqueness index + SHA-256 finding set fingerprint deduplication.

---

### Next Steps for Terminal (`cc`)

The debate is **CONVERGED**. Terminal `cc` can now consolidate the ledger and this Round 3 final response into the canonical `peerhub` target architecture design document.
