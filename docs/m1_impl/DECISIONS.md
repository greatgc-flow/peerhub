# M1 implementation decisions (orchestrator)

## Wave 0 gate (ag.pro, 2026-10-03): BLOCK on vacuous SCH-011/012/013 persistence asserts for record/offset
- D-W0-1 (Q-W0-2): Record/Offset persistence port is built in Wave 1. SCH-011/012/013 for record/offset MUST be re-asserted with real
  persistence (row counts and revisions unchanged on rejection) as part of Wave 1; Wave 1 exit requires it. The catalog is not amended.
- D-W0-2 (Q-W0-3): schema-only validation for extension-owned models (Observation, Resource Pool) is accepted in Wave 0; persistence
  assertions are enforced in Wave 5.
- D-W0-3 (Q-W0-4): moving the CLI composition root to `peerhub/m1_cli.py` is accepted (Core entrypoints must not import extensions).
- D-W0-4 (Q-W0-5): ARCH-004 on the ReadonlyDiag class is accepted for now; module split and module-level re-check in Wave 5.
- D-W0-5 (Q-W0-6): digest scope is decided in Wave 1 strictly from CORE_CONTRACT.md / STREAM_RECORD_OFFSET.md / TD-20; no ad-hoc scope.

## Wave 1 gate (cx.pro, 2026-10-03): BLOCK, fixes required; rulings
- D-W1-1 (Q-W1-1): TD-20 wins (scope = stream+author+key; corroborated by IDEM-001/002). PROP-002 as written in the catalog contradicts TD-20:
  tests assert conflict on mutations within an unchanged scope, and independent Records for author/stream changes. SPEC ERRATUM to report to the
  spec owner (user) in the final report; do not edit the frozen catalog.
- D-W1-2 (Q-W1-2): no asymmetric default. Follow `record.schema.json` required fields: if `created_at` is required on the wire, append rejects an
  omission; stored Record digest must be reproducible from the stored Record.
- D-W1-3 (Q-W1-3): member changes on a CLOSED stream allowed via revision CAS; append and reopen forbidden (TD-09).
- D-W1-4 (Q-W1-4): peer upsert preserves identity and original created_at; mutable fields replaced; no conflict semantics (document).
- D-W1-5 (Q-W1-5): no body size limit; exact preservation required (PROP-009).
- Positions may have gaps (TD-01): CORE-005/PROP-010 must assert on actual committed positions, not gapless sequences.

## Wave 2 gate: cx.pro BLOCK -> fixed -> ag.pro APPROVE (816093d). Rulings Q-W2-1..6 accepted (see wave2 gate notes in reports).

## Wave 3 gate (cx.pro, 2026-10-03): BLOCK on f8741d5; rulings
- D-W3-1 (Q-W3-1): BRG-015/CERT-003 stay in the default gate (deterministic); catalog tier e2e kept for traceability.
- D-W3-2 (Q-W3-2): reconcile body `{decision:"RETRY", delivery_id}` accepted as convention, AUTHENTICATED against the durable Record (persisted kind/body/author), never against caller copies. ACCEPT/ABANDON deferred.
- D-W3-3 (Q-W3-3): "unlisted in STM coverage" does NOT mean forbidden. Forbidden = downgrade of certainty and any overwrite of TERMINAL truth (TD-11).
  Forward edges backed by evidence (NOT_STARTED->MAY_HAVE_STARTED->STARTED->TERMINAL, and MAY_HAVE_STARTED->STARTED/TERMINAL on late evidence) are allowed;
  NOT_STARTED->TERMINAL only with an explicit pre-spawn-failure result. SPEC GAP to report to the spec owner (user): the transition contract is not exhaustive.
- D-W3-4 (Q-W3-4): ACTIVE/FRESH + logged LOST accepted; the constant "default" binding is rejected: persist and test real binding compatibility (SESSION_BRIDGE.md mapping contract).
- D-W3-5 (Q-W3-5): CTX budget/truncation deferred to Wave 4 (TD-12).
- D-W3-6 (Q-W3-6): terminal result dict/key/digest convention accepted; VALIDATE before terminal commit; callbacks bound to a specific delivery.
- D-W3-7 (Q-W3-7): no ordinary-message fallback for control Records; until Wave 4, unsupported control Records stay pending or fail explicitly with no runtime delivery/ack.

## Wave 3 re-gate: ag.pro APPROVE at 2fbf4d2.
- D-W3-8 (Q-W3-8): about_to_invoke marker (certainty MAY_HAVE_STARTED before invocation); adapter-reported PrespawnError is the single permitted return to NOT_STARTED. Safe: a crash before the revert persists leaves the conservative MAY_HAVE_STARTED. Needs spec-owner confirmation (report at the end).
- Pending control Records block later Records for that peer/stream until Wave 4 (accepted as interim).

## Wave 4 gate (cx.pro, 2026-10-03): BLOCK on d1945ca; rulings (conservative; items marked OWNER need spec-owner confirmation at the end)
- D-W4-1 control fencing: a control effect must be fenced by the current delivery token AND lease; the target delivery is persisted BEFORE the effect and never retargeted on recovery; an expired lease cannot commit `done`; a superseded handler cannot execute its effect.
- D-W4-2 gating: pause/cancel committed at any point before the runtime invoke (incl. at `bridge.before_runtime_invoke` and during session creation retries) must stop delivery/creation.
- D-W4-3 stranded controls: an `in_progress` control whose lease expired must be revisited/recovered; Offset consumption must not hide a NULL outcome.
- D-W4-4 redirect catch-up honours target scope and the injected budget (max_records=0 yields none) and carries boundary metadata.
- D-W4-5 boundary Record: validate an existing Record at the deterministic key (kind/body/author) instead of swallowing conflicts; a foreign Record at that key is an explicit error.
- D-W4-6 new-direction: no success on partial failure (close-CAS exhaustion, invalid payload must leave no empty stream); report precise failure.
- D-W4-7 (OWNER, Q-W4-2): cancel does NOT skip earlier unread Records. CTL-003: Offset unchanged; TD-10 defers administrative skip. Cancel terminates the running delivery only; later Records still flow. Revert the skip behaviour.
- D-W4-8 (OWNER, Q-W4-5): CTX-001 says "after Offset": the bridge catch-up uses the peer Offset, not after_position=0.
- D-W4-9 (OWNER, Q-W4-9): control precedence is by Record POSITION (latest control decision by position wins, e.g. an older RETRY never overrides a newer cancel); deterministic across crashes. `paused` display precedence over `blocked_uncertain` accepted.
- D-W4-10 (OWNER, Q-W4-4): control handling idempotent per (Record, peer); repeated steer contract to be confirmed by the owner.

## Wave 4 re-gate (cx.pro) on 257a758: BLOCK on 3 gaps + continuity design; rulings
- D-W4-8b (supersedes the Offset-window reading of D-W4-8): Offset governs DELIVERY only. A FRESH generation (session/context loss) is bootstrapped with a bounded, ordered history suffix of the Stream up to (excluding) the Record being delivered, within the injected TD-12 budget, with explicit boundary metadata; when history fits the budget it must be included in full. Pinned unseen redirects stay as additional intent. CTX-003 / LONG_HORIZON_CONTINUITY: lost context must be recoverable from the Stream. (OWNER: confirm the CTX-001 'after Offset' wording means the projection helper's cursor semantics, not the bridge bootstrap.)
- D-W4-2b: gate pause/cancel before the FIRST create_session attempt too (not only between retries).
- D-W4-5b: validate the COMPLETE boundary payload (reason, catch_up shape/content), not only kind/peer/generation.
- D-W4-9b: RETRY1 -> cancel -> RETRY2: the latest authorized decision by Record position wins; no INSERT OR IGNORE retention of an older RETRY; after restart the state follows the latest decision.
- (b) accepted: superseded runner's session interruption is best-effort provided its authoritative writes (terminal/response/Offset) are fenced.

## Wave 4 closed (orchestrator, 2026-10-04) at 14c9e6e
- cx.pro blocked 3 rounds; ag.pro adversarial gate found 5 claims (4 real, fixed; claim 5 refuted with evidence: recovery reads bridge_controls, not the unread window). No further per-wave re-gate (diminishing returns); cx.pro final review covers waves 0-7 once (see CX_FINAL_CHECKLIST.md).
- OWNER items accumulated: Q-W4-11 (boundary budget change after restart conflicts), Q-W4-12 (pins make the fresh projection non-contiguous), CTX-001 'after Offset' wording, cancel semantics, control precedence.


## Wave 5 (orchestrator, 2026-10-04): decisions taken while implementing (see OPEN_QUESTIONS.md Q-W5-*)
- D-W5-1: Observation + Diag share the workspace SQLite file (one snapshot, real FK); catalog `e2e`-tier OBS-013/014 and DIA-010 stay in the default gate (integration marker), as BRG-015 (D-W3-1).
- D-W5-2 (D-W0-4 closed): ReadonlyDiag has its own module `peerhub/extensions/diag.py`; ARCH-004 now checks the WHOLE module (imports allow-list: stdlib minus process/OS/network modules + `observation_model` + `m1.models`; banned writer names; SQL literals; `open()` modes; every `sqlite3.connect` is read-only URI) and the checker itself is mutation-tested.
- D-W5-3 (D-W0-2 closed): Observation / Resource Pool wire parsing has persistence assertions (rejections leave full state digest, row counts and table digests unchanged; positive controls prove the ports write).
- D-W5-4 (TD-13/TD-23): freshness/latest clock = local captured_at else observed_at; age >= TTL is STALE; ties broken by the persisted AUTOINCREMENT capture ordinal; source skew retained (`skew_seconds`).

## Wave 5 closed (orchestrator, 2026-10-04) at 94f48f9
- ag.pro adversarial gate: 2 findings (static ARCH-004 bypass -> runtime sqlite authorizer + dynamic-SQL static flag; resource_pools INSERT OR REPLACE -> no-replace trigger + audit), both fixed with probes. No further re-gate; cx final review covers waves 0-7.
- OWNER items (Q-W5-*): TTL numbers (300s default; spec gives none), negative-age handling (UNKNOWN), trust of wire captured_at, measurement-key exclusion list for non-MEASURED states, source-declared quota/rate-limit semantic, subject_ref not checked against peers.

## Wave 6 (orchestrator, 2026-10-04): see OPEN_QUESTIONS Q-W6-*
- D-W6-1: Core fails closed on read-only/full/corrupt storage with typed errors, unchanged state and no repair/recreate (FLT-011/012/013); busy stays raw OperationalError (MP-001).
- D-W6-2: session mappings are bound to the workspace generation (FLT-008, OWNER Q-W6-1). D-W6-3: Core CLI works with extensions absent (E2E-009).

## Wave 6 closed (orchestrator, 2026-10-04) at 12d33ef
- ag.pro review (reworded as code review; the 'adversarial' wording triggered a refusal): 4 claims, 2 real + 1 design (quick_check size threshold, OWNER Q-W6-2), 1 false (old-schema), survivors confirmed killed by earlier waves. No further re-gate; cx final review covers waves 0-7.
- OWNER items: Q-W6-1 generation-bound sessions, Q-W6-2 quick_check threshold (256 MiB), exit codes 4/5.

## Wave 7 (2026-10-04)
- D-W7-1 (TD-16, MIGRATION_CUTOVER step 7): importer = dry-run first (writes nothing, does not create the target), explicit apply with optional plan digest, source opened immutable read-only, one transaction per correlation group, idempotency verified against persisted rows (marker + record keys/digests, prefix match), conflicts/malformed reported not guessed. See Q-W7-3.
- D-W7-2 (MIG-003, TD-14, RELEASE_PROMOTION_ROLLBACK 1-4): a future schema is refused with an actionable message (no journal-mode change, no bytes written), CLI exit 6; Diag reports FAILED; importer refuses a future-schema target. No downgrade path is provided.
- D-W7-3 (TD-18): see Q-W7-1.

## Wave 7 closed (orchestrator, 2026-10-04) at d6b36c3
- ag.pro review: importer later-offsets (real, fixed per component; conflicts reported, never auto-advanced: OWNER Q-W7-11), forced UTF-8 (real, fixed: tolerant streams), 3 survivor claims refuted/strengthened. Branch ref was behind a detached HEAD; moved to the HEAD (no history lost). cx final review covers waves 0-7 once.
- Next: Wave 8 (LIVE canary) = real adapters (T1) + opt-in live tests, run ONCE with minimum quota (lowest-tier profiles, tiny prompts, no retries; cx at most once).

## Owner-item consultation (ag.pro + cx.effort, 2026-10-04) - delegated by the user ("협의통해 진행")
Consensus (both): A1 confirm TD-20 (record PROP-002 erratum via lifecycle FEEDBACK_RECORD, frozen catalog untouched); A3 confirm (document departure from CTX-001 suffix wording; dedupe/canonical order/omission+pin metadata within one total budget); B6 accept partial importer with explicit unmapped report + documentation; B7 accept no real resume/interrupt/steer for M1 (ACCEPTANCE_DOD + LIVE-004: report adapter capabilities separately from vendor capabilities; unsupported cancel never claims process termination); B8 confirm cancel semantics; C accept defaults (Python/OS matrix provisional: mark unexecuted cells UNVERIFIED per TD-18).
Split (cx.effort wins, strict TD-11): 
- D-OWN-A2: pre-spawn failure stays NOT_STARTED -> NOT_STARTED (retryable, Offset unchanged); NOT_STARTED->TERMINAL is NOT allowed (BRG-011). Late evidence fenced (TD-25).
- D-OWN-A4: replace the certainty-based about_to_invoke marker with a SEPARATE persisted invocation marker (certainty stays NOT_STARTED while invocation is pending; an unresolved marker blocks replay and recovery promotes it to MAY_HAVE_STARTED; a fenced proven pre-spawn failure or control halt RESOLVES the marker without downgrading certainty). No certainty downgrade anywhere (the earlier D-W3-8 single-exception is superseded). Needs schema upgrade compatible with existing stores + crash/control-race regressions.
