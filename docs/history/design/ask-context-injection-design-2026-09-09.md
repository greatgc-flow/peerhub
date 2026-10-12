# Design Note: Direct Ask Context Injection, Session Lifecycle, Resilient Dispatch, and Consensus Effects (Item B)

**Date**: 2026-09-09  
**Status**: Ratified / Implementation Design  
**Context**: Backlog Item B (P0) from `docs/reviews/p-drive-mece-migration-audit-2026-09-09.md` (Section 5), absorbing C2 (Directive Consumer) and D1 (Automatic Lesson Injection).  
**Parity Target**: `P:\_sys\core\hub.py` lines 2442-2495 (context layers: user directives, runtime directives, peer-filtered lessons, room context, handoff continuity, task state).

---

## 1. Architectural Call Chain in `direct_ask.py`

In `peerhub/application/direct_ask.py`, `execute_direct_ask()` coordinates the pipeline:

1. **Target & Policy Resolution**:
   - `target = resolve_peer_target(request.peer_name, profile_id=request.profile_id)`
   - `model_binding = ModelConfigService(runtime.peer_registry_service).resolve(...)`
2. **Context Assembly (`assemble_ask_prompt`)**:
   - Injection occurs **after** `create_runtime()` has initialized the governance, room, task, and directive services, and **before** `AdapterRequest` is instantiated.
   - Query `DirectiveService` for active directives (`lifecycle == "ACTIVE"`).
   - Check for workspace user directives (`_sys/ai/user-directives.md`).
   - Query `inject_lessons()` from `peerhub.application.lesson_inject` using `runtime.governance_broker`.
   - Query `RoomsService` for room summary (`get_room_summary`) and continuity handoff (`context_fill`).
   - Query `TaskService` for active task context (`get_target`).
   - Format prompt according to peer context policy (`query_first` for context-fragile peers like `ag`; context-leading for standard peers like `cc`, `cx`).
3. **Session Resolution (`resolve_session_state`)**:
   - Compute `adapter_fingerprint` incorporating `target.profile.profile_id` and the resolved `model_binding` (`model_id`, `selection_mode`, `reasoning_effort`).
   - Determine `SessionAction` (`RESUME` vs `CREATE` vs `NONE`) and populate `SessionHint`.
   - Stale model detection: if model binding has changed since the session was established, force `SessionAction.CREATE` to prevent silent corruption.
4. **Adapter Request Construction**:
   - Populate `AdapterRequest(prompt_content=assembled_prompt, requested_session_action=session_action, ...)`
5. **Resilient Dispatch Loop**:
   - Wrap initial dispatch in `AttemptDispatchPlan`.
   - Replace bare `dispatch_and_execute()` with `runtime.application_workflows.dispatch_with_retries()`.
   - Use `DirectAskRetryTargetResolver` to enable failover.
   - If execution fails (provider or process error), invoke `runtime.health_service.classify_and_open_circuit()` with typed `HealthStageObservation`.
6. **Session Persistence**:
   - On successful response or session identity capture, persist the updated session binding to `state_store`.

---

## 2. Prompt Content Before vs. After Injection

### Before (Raw Prompt):
```text
say hello in two words
```

### After (Multi-Layer Injected Prompt):

#### Context-Fragile Peers (`ag`, Query First):
```markdown
[USER QUERY]
say hello in two words

[USER DIRECTIVES]
- Adhere to immutable governance invariants.

[RUNTIME DIRECTIVES]
- [DIR-01] Always verify file existence before editing.

[PEER LESSONS]
- HIGH L-101: Never run git commit directly from scripts.

[HUB CONTEXT]
Room ID: room-123
Members: ag, cc, cx
Mission: Fix regression in scrubber
Blocked: none
Phase: active

[HANDOFF]
## GOAL
Fix regression in scrubber

## RECENT_COMPLETED
- Root caused bug 1

[TASK CONTEXT]
Task ID: task-001
Summary: Inspect scrubber line 42
Phase: in_progress
State: running
```

#### Standard Peers (`cc`, `cx`, Context First):
```markdown
[HUB CONTEXT]
Room ID: room-123
Members: ag, cc, cx
Mission: Fix regression in scrubber
Blocked: none
Phase: active

[USER DIRECTIVES]
- Adhere to immutable governance invariants.

[RUNTIME DIRECTIVES]
- [DIR-01] Always verify file existence before editing.

[PEER LESSONS]
- HIGH L-101: Never run git commit directly from scripts.

[HANDOFF]
## GOAL
Fix regression in scrubber

## RECENT_COMPLETED
- Root caused bug 1

[TASK CONTEXT]
Task ID: task-001
Summary: Inspect scrubber line 42
Phase: in_progress
State: running

[USER QUERY]
say hello in two words
```

*Note*: If no directives, lessons, room, or task context exist, the prompt remains unpadded `request.prompt`.

---

## 3. Session Lifecycle: Resume vs. Fresh Selection

- **Invariant**: `SessionHint.adapter_fingerprint` MUST include the sha256 hash of:
  `f"{peer_kind}:{profile_id}:{model_id}:{selection_mode}:{reasoning_effort}"`
- **Resume Criteria**:
  1. `request.resume is True` (or `request.session_action is SessionAction.RESUME`, or `request.session_id` provided).
  2. A durable `SessionBinding` exists in `state_store` for the key `(workspace, instance_id, profile_id, conversation_scope)`.
  3. `existing_binding.adapter_fingerprint == current_fingerprint`.
  If all 3 match:
  -> `SessionAction.RESUME`, `SessionHint(external_session_id=existing.session_id, adapter_fingerprint=current_fingerprint, session_generation=existing.session_generation)`.
- **Incompatible / Stale Model Fallback**:
  If model configuration changed (e.g. switched model ID or reasoning effort tier):
  -> Incompatible session detected. Force `SessionAction.CREATE` with generation increment: `SessionHint(external_session_id=None, adapter_fingerprint=current_fingerprint, session_generation=existing.session_generation + 1)`.
- **Fresh / Unmanaged Session**:
  If `session_id` or session tracking is requested without existing session:
  -> `SessionAction.CREATE`.
  If unmanaged one-off dispatch:
  -> `SessionAction.NONE`, `session=None`.

---

## 4. Resilient Dispatch & Health Circuit-Breaker Integration

- **Bounded Outer Retry**:
  - `execute_direct_ask()` invokes `dispatch_with_retries()` with `max_attempts` (defaulting to 3, configurable via `request.limits.max_attempts` or configuration).
  - Target failover is supported via `DirectAskRetryTargetResolver(workspace_root=...)` which dynamically resolves alternative eligible peer targets from the registry.
- **Circuit Breaker Integration**:
  - When an attempt encounters a fatal transport or provider failure (process return code != 0, silence timeout, executable missing, or provider connection failure):
  - Directly construct `HealthStageObservation(stage=HealthStage.CALL_PROVIDER, status=HealthStageStatus.FAILED)`.
  - Call `runtime.health_service.classify_and_open_circuit()` with:
    - `attempted_trace=(HealthStageObservation(...),)`
    - `evidence_subject=EvidenceSubject(scope=PolicyScope.PROFILE, subject=target.profile.profile_id)`
    - `receipt=PolicyReceipt(incident=f"direct-ask-{command_id}", gate_generation=1, timestamp=clock.now(), fingerprint=f"direct_ask_failure:{command_id}")`
  - This transitions the circuit to `CIRCUIT_OPEN` or enforces backoff dynamically through peerhub's native health projection.

---

## 5. Consensus & Final-Call Observable Effects

- **Problem**: `ConsensusService._submit()` previously defaulted `effect_intent` to `EffectIntent(kind="consensus.noop", payload={})`. Resolved rounds and Final Call acknowledgements resulted in inert no-ops.
- **Solution**:
  1. `final_call_ack()`: when `final["complete"] is True`, emit `EffectIntent(kind="consensus.resolved", payload={"round_id": round_id, "outcome": "approved", "basis": "all final-call acknowledgements agree", "resolved_by": actor_id, "decision_hash": digest, "final_call_complete": True})`.
  2. `resolve()`: emit `EffectIntent(kind="consensus.resolved", payload={"round_id": round_id, "outcome": outcome, "basis": basis, "resolved_by": resolved_by, "decision_hash": digest, "final_call_complete": bool(...)})`.
  3. `reject_on_dissent()`: emit `EffectIntent(kind="consensus.resolved", payload={"round_id": round_id, "outcome": "rejected", "basis": basis, "resolved_by": rejected_by, "decision_hash": digest, "dissent": True})`.
  4. `abandon()`: emit `EffectIntent(kind="consensus.abandoned", payload={"round_id": round_id, "reason_code": reason_code, "reason": reason, "abandoned_by": abandoned_by})`.
  5. `ConsensusService.process_consensus_effects(round_id)`: provides execution and projection of pending consensus effects from the broker outbox, writing an observable `consensus-resolution:{round_id}` target or effect receipt, ensuring full observability.

---

## 6. Configuration Knobs (No Hardcoding)

All knobs are config-driven using existing schemas:
- **Lessons Policy**: `LessonInjectionPolicy(min_severity="medium", max_chars=1200, max_items=8, critical_always_include=True)` and `LessonInjectionContext(os=..., shell=..., task_types=...)`.
- **Directives Count/Char Limit**: bounded by existing directive limits (max 10 directives, max 2000 chars).
- **Retry Count**: derived from `TransportLimits.max_attempts` or defaults to 3.
- **Circuit Breaker Thresholds**: governed by native `HealthPolicy` in `HealthService`.
