# Round 2 cross-review: PeerHub simplification

Author: cx.astra. Date: 2026-09-25. **Verdict: REJECT.**

The simplification direction is sound. This plan is not ready to implement: it contains stale behavioral claims, removes dependencies of tonight's active work, omits CompletionContract, and mistakes existing synchronous transactions for a proposed replacement. Rejection applies to this revision, not to the user's principle that very simple is best. Existing code and tests alone are not reasons to retain a feature; the reasons below identify current callers or the explicitly active consensus/quarantine work.

Reviewed [the complete Round 1 proposal](PEERHUB-SIMPLIFICATION-R1-ag-deepthink-2026-09-25.md), SHA-256 `b82beeda777584b3d22959ca5592871f9a695772a718f54d63225566d855f79d`, against source HEAD `2144dd9d4052944c151b80a5731c93b71f4697cf`. HEAD stayed unchanged throughout the review. The proposal was untracked at review start. No production source or existing tests were changed, and no peers were dispatched.

**HIGH: the conflict-avoidance section does not protect the active consensus work.**

The physical ratified file is [CONSENSUS-REPLACEMENT-R1-ag-deepthink-2026-09-25.md](CONSENSUS-REPLACEMENT-R1-ag-deepthink-2026-09-25.md); its heading says R13. The R13 *filename* cited by section 4 does not exist. Commit `2144dd9` adds B1 RED specs; [ConsensusStateMachine.evaluate](../../peerhub/governance/consensus.py#L839) still raises `NotImplementedError("RED phase")`. All 35 B1 tests reproduced that failure [empirical_probe].

Protecting just `consensus.py`, `proposals.py`, and their tests is insufficient:

- Phase 2/item 11 changes the common command gateway and authorization used by native and proposal commands. That crosses the active credential/public-ingress contract.
- Phase 3/item 14 removes the arbiter producer, while B1 explicitly tests arbiter attachment and B8 maps that public path.
- B5 requires authority-version checks in the broker transaction, public command-to-receipt binding, and pre-invocation revalidation; B6 requires approval plus effect intent in one atomic submission. See [the ratified design, lines 117-151](CONSENSUS-REPLACEMENT-R1-ag-deepthink-2026-09-25.md#L117).

Even after consensus completion, item 18 cannot remove these semantics simply because it says "Post-Consensus." A smaller implementation must preserve them, or the consensus design must be explicitly revised. Until then, items 11/14/18 need a dependency on the active consensus contract, not merely a promise to avoid two files. CLI changes affecting its parser/handlers/tests need the same coordination boundary.

**HIGH: replacing operational errors with logging removes the producer of tonight's quarantine fix.**

[OperationalErrorService.report_error](../../peerhub/governance/operational_errors.py#L93) maintains a CAS-protected per-peer/pattern counter. At and above the threshold it creates distinct `quarantine-review` targets with `REQUESTED` status, trigger count, and a report snapshot ([lines 169-239](../../peerhub/governance/operational_errors.py#L169)). [QuarantineReviewCoordinator](../../peerhub/application/quarantine_review.py#L83) consumes those targets. The production CLI's `error report` and `error review resolve` paths both use this pipeline.

Thus Phase 3/item 13 and Phase 4/item 17 are coupled. Plain logging preserves neither review creation nor review identity. This is a concrete dependency of the specifically requested ESCALATE work, not a hypothetical feature-retention argument. Keep the small counter/review producer when simplifying storage, or explicitly retire the entire workflow as a changed direction. Item 13 cannot have dependency `None` while claiming that workflow remains intact.

Item 17 is also based on a false distinction: the current coordinator already calls [HealthService.open_manual_quarantine synchronously](../../peerhub/application/quarantine_review.py#L141). It commits `PENDING_ESCALATE`, applies the health mutation, and reconciles the review with the circuit receipt. A direct call alone does not replace the durable review state, authority dominance check, or recovery identity. The pending and health changes currently use separate transactions; a one-transaction replacement would need an explicit shared unit of work across both writers.

Two isolated failure-injection probes confirmed [empirical_probe]:

| Injected failure | Durable state before recovery | Explicit recovery result |
|---|---|---|
| Before the health call, after pending commit | `PENDING_ESCALATE`; no circuit | A reconstructed coordinator opens MANUAL quarantine, records its receipt, and finishes `ESCALATED`. |
| After health application, before reconciliation | `PENDING_ESCALATE`; circuit open | Recovery reuses the exact existing circuit receipt and finishes `ESCALATED`. |

These use the real SQLite-backed services with a reconstructed coordinator; they are handled-failure probes, not abrupt process-crash tests. Also, `resume_pending_reviews()` has no production caller in the searched source. Do not claim automatic startup recovery is already wired. The existing tests named QR-E-11 and QR-E-12 only check a nonexistent review and an empty recovery pass respectively ([test source](../../tests/unit/application/test_quarantine_review_policy.py#L257)); their names do not prove those crash boundaries. This coverage limitation matters when promising to preserve tonight's fix.

**HIGH: the broker proposal misidentifies the current transaction model and effect surface.**

[GovernanceBroker.submit](../../peerhub/governance/broker.py#L311) is synchronous. It performs idempotency lookup, revision validation/CAS, and commits target, mutation, transition receipt, command binding, and event together. [SqliteUnitOfWork](../../peerhub/persistence/sqlite.py#L914) already uses `BEGIN IMMEDIATE` ([line 966](../../peerhub/persistence/sqlite.py#L966)). There is no demonstrated distributed two-phase commit to replace.

The deferred part is effect delivery/recovery. [ProposalCoordinator](../../peerhub/application/proposals.py#L412) actually searches unfinished effects and invokes the invariant projector. [Broker recovery/claim/result](../../peerhub/governance/broker.py#L457) distinguishes unclaimed, claimed, and receipted work. A SQLite commit cannot by itself atomically complete a subprocess or a file effect. Current persistence also uses `event_log`/`effect_deliveries`; [migration 0017](../../peerhub/persistence/migrations/0017_drop_legacy_outbox.sql#L4) already dropped the legacy outbox tables.

The broker is instantiated and injected into consensus, rooms, tasks, lessons, directives, peer registration, feedback, operational errors, and other services in [runtime.py](../../peerhub/runtime.py#L215). It is neither an unused debug class nor an async daemon. Removing the class may still be desirable, but item 18 needs a bounded replacement for the real semantics and storage, not "use synchronous SQLite." No-op effects such as `feedback.noop` and `quarantine-review.noop` are separate pruning candidates; deleting those does not require deleting recoverable invariant effects.

**HIGH: direct service calls and OS permissions do not replace the authorization checks being removed.**

[ApplicationAPI.submit](../../peerhub/application/api.py#L428) validates version/method/parameters and required idempotency, then calls [GovernanceAuthorizer](../../peerhub/application/governance_authorizer.py#L47). A presented credential must verify against its actor; missing verification fails closed. Without a credential, asserted client identity must match. Current room/proposal CLI integration tests demonstrate rejection of mismatched identities/credentials. Direct `RoomsService` calls do not perform this gateway check.

[CapabilityLease](../../peerhub/dispatch/capability.py#L178) is separate from process/session/duty leases. Its checks bind the current command, attempt, principal, target/profile, route, policy, and permission tier. [ApplicationWorkflows](../../peerhub/application/workflows.py#L676) validates it before planning; dispatch intent revalidates it again. All peers sharing an OS user is precisely why OS permissions alone do not express those distinctions. Focused production-workflow tests confirmed rejection before spawn when enforcement evidence is absent, successful READ_ONLY dispatch to that same peer, and rejection after a policy change between planning and intent [empirical_probe].

Items 11/15 may collapse wrappers and proof objects, but must first identify the minimal common checks that survive and where they run. Migrating to Typer is not a prerequisite for doing that. Do not bypass the gateway in Phase 2 and only consider authority preservation in Phase 4.

**HIGH: ArtifactMaterializer is not just an oversized-prompt tempfile wrapper.**

[ArtifactMaterializer](../../peerhub/dispatch/materializer.py#L189) verifies content digests, stages and atomically renames files, handles existing-file recovery, and resolves competing materialization results. [Artifacts](../../peerhub/dispatch/artifacts.py#L52) also validates workspace scope/path containment and builds substituted invocation arguments. The production [workflow](../../peerhub/application/workflows.py#L715) declares manifest metadata, materializes, then reserves verified artifacts with the dispatch intent ([line 871](../../peerhub/application/workflows.py#L871)).

The artifact success/failure workflow tests and digest/recovery/CAS materializer tests passed [empirical_probe]. `tempfile` can simplify naming/lifetime, but it does not by itself replace these current dispatch dependencies. Item 16 needs a decision on the artifact contract and reserve/verify boundary; it does not depend on CLI routing item 11. A smaller file helper is a valid goal once those concrete requirements are separated from optional lifecycle machinery.

**MEDIUM: several CLI findings are already fixed or are overstated.**

| Claim | Verified current source / behavior | Review consequence |
|---|---|---|
| About 30 root commands, ~5,000 lines / 236 KB | Exactly **30** root commands by parser inspection; `cli/__init__.py` is **4,974 lines / 236,007 bytes** [empirical_probe]. | Size claim is correct. It is not all argparse boilerplate: `main` is 1,482 lines including dispatch, and command bodies account for much of the rest. Existing `cli/commands/daily.py` and `setup.py` already demonstrate extraction without a new framework. |
| `statusline` ignores `--peer` | [`_run_statusline`](../../peerhub/cli/__init__.py#L499) branches on it. Direct invocations returned ag=0, cc=2, cx=2 with explicit unsupported-formatter stderr [empirical_probe]. Existing regression test is [here](../../tests/integration/cli/test_cli.py#L1258). | Correct the rationale. Deletion can still follow the simplicity rule: no current hook consumer of this PeerHub command was found in the inspected integration surface. |
| `diag --live` runs `cls` | It already writes ANSI at [`daily.py:376`](../../peerhub/cli/commands/daily.py#L369). The [Windows live-loop test](../../tests/unit/cli/test_diag_broadcast.py#L34) passed and asserts no subprocess call [empirical_probe]. | Item 09 is already done; it has the wrong target file and should leave the implementation backlog. |
| Merge reactions into `react --remove` | [`react --remove`](../../peerhub/cli/__init__.py#L4408) already exists and shares a handler with `unreact` ([line 2625](../../peerhub/cli/__init__.py#L2625)). CLI reaction tests passed. | Item 08 is only deletion of the redundant `unreact` spelling. |
| `duty` always requires a room | [`duty sweep`](../../peerhub/cli/__init__.py#L4438) operates across rooms and has no `--room-id`; room session open/heartbeat/close do require it. | Nesting is possible, but do not impose a new mandatory room filter on sweep. |
| `gate` is 100% duplicate status visibility | [`gate check`](../../peerhub/cli/__init__.py#L1696) is a named-peer predicate returning 0/1 for open/closed, not merely a status display. `health precheck` is the closest replacement; the integration test verifies closed gate exits 1. | Remove the separate root if desired, documenting the replacement command and deliberate output/exit changes. Do not justify deletion solely with `peer status`. |
| `thread-new` duplicates `create-thread` | The [compatibility adapter](../../peerhub/application/thread_new.py#L22) generates legacy behavior: slug/default creator and successful duplicate guidance; native create uses create-only CAS. Relevant tests passed. | Deletion is still reasonable absent a real compatibility consumer, but it drops distinct semantics rather than removing a literal alias. |
| `adapter discover` is orphaned | Registered in [`cli/commands/setup.py`](../../peerhub/cli/commands/setup.py), documented in [README](../../README.md#L11), and executable through main. | Group relocation is fine; "orphan" is not supported. Update the actual module and documented callers. |

The inspected active Antigravity hook is `_sys/antigravity/config/statusline-command.sh`, selected by its settings. It delegates to `_sys/ai/common/statusline/statusline-unified.sh`, not `peerhub statusline`. The unified script writes `_sys/data/temp/ag_statusline_stdin.log`, which telemetry consumes. Thus pruning PeerHub's formatter must not be expanded to deleting that separate telemetry input under item 01's vague "internal hooks." This is static integration evidence, not a claim about every external user's installed hooks.

**MEDIUM: the component names/citations and MECE/dependency table need a rewrite.**

`CommandEnvelope` really exists at [`core/protocol.py:414`](../../peerhub/core/protocol.py#L414). `ApplicationAPI` exists; there is no `CommandBus` class. `application/legacy.py` is now a compatibility re-export shim, not the claimed active CLI-to-envelope mapper; [`Client.submit`](../../peerhub/client.py#L37) constructs the envelope. There is no `ArtifactManifest` class: distinguish `MaterializationManifest`, `MaterializationItemRequest`, and `ArtifactManifestRecord`. There is no `RoomService.create_session()` API; the actual service is `RoomsService` and room participation has separate session services. These names can be explanatory shorthand only if explicitly labeled as such.

The 18 rows are not a complete, mutually separated work breakdown:

- **Missing scope:** no verdict for `CompletionContract`, explicitly requested in this audit. Native [ask](../../peerhub/application/direct_ask.py#L603) and [broadcast](../../peerhub/application/broadcast.py#L261) construct DELIVERY_ONLY contracts. The generic wire decoder accepts other kinds, while [production assessment](../../peerhub/application/workflows.py#L1140) passes no requirement evaluations. Preserve response-present/truncation/error distinctions; explicitly audit/cut unsupported or unused non-delivery ceremony after checking stored records and real callers. Do not silently retain all six kinds because unit tests cover them.
- **Undefined inventory:** the 30-command universe has no KEEP/drop disposition for the unlisted groups. A comprehensive CLI audit needs that inventory; the present table is a selected-candidate list.
- **Overlapping responsibility:** item 17 migrates a broker writer, while item 18 owns "all writers." Split the quarantine semantics decision from the shared transaction implementation. Item 10 also bundles parser extraction and dependency/framework migration, while item 11 separately changes execution semantics; these are independent changes.
- **Missing cleanup ownership:** deleting only `governance/feedback.py` leaves runtime construction, API registration, commands/handlers, compatibility exports, CLI, and docs importing it. Table 12 must own that complete vertical removal. Equivalent ownership is missing for 13/14/15/16/18 and their persisted data.
- **Missing edges:** item 13 precedes/feeds 17; item 14 intersects active consensus; item 11 must preserve authorization before bypass; item 18 spans all remaining writers and the B5/B6/B8 storage/effect contract. The single `17, Consensus-Replacement` dependency is insufficient.
- **Invented edges:** 01-08 are not prerequisites for parser extraction/framework choice; a direct-call design need not follow Typer; capability/artifact changes do not depend on a CLI rewrite. These can be scheduling preferences, not technical dependencies.
- **Internal staging drift:** item 08 has no Phase 1 bullet, item 09 is already implemented, and item 17 says `None` in the table while the prose puts it in the post-consensus phase. A single authoritative staging list should replace these inconsistent views.

**Disposition of all 18 items**

These are review recommendations, not implementation authorization. "Simplify" means preserve only the specific current dependency identified above, not every old interface.

| ID | Review disposition | Required correction / actual dependency |
|---|---|---|
| 01 | **DELETE candidate supported; amend rationale/scope** | Peer flag works. No inspected session hook calls this command; preserve the separate ag stdin telemetry hook. Remove formatter/registration/docs together. |
| 02 | **SIMPLIFY** | Consolidate named-peer gate behavior under health or explicitly retire its shell contract after checking callers. It is not just peer-status output. |
| 03 | **DELETE candidate supported** | Explicitly retire slug/default-creator/duplicate-no-op compatibility; do not call it behaviorally identical. |
| 04 | **SIMPLIFY** | Nest room participation/duty commands; keep cross-room sweep semantics. Include duty/session command registrations and callers. |
| 05 | **SIMPLIFY** | `alert raise` really has room scope; preserve its existing dispatch/audit behavior during relocation. |
| 06 | **SIMPLIFY** | Move discovery if desired; update `cli/commands/setup.py`, README, and parser tests. |
| 07 | **SIMPLIFY** | Move effect-status visibility into diagnostics; preserve current `diag --live/--json/--domains` behavior. Broker status is real unfinished-effect data, not an obsolete class leak. |
| 08 | **DELETE redundant alias** | `react --remove` already exists; only `unreact` removal remains. |
| 09 | **DONE / remove backlog item** | ANSI replacement already exists in `cli/commands/daily.py`. |
| 10 | **SIMPLIFY extraction; drop mandatory framework migration** | Continue existing argparse module extraction first. Typer is an optional separately justified choice; no measured reduction/type-safety case was supplied. |
| 11 | **SIMPLIFY only after the authorization contract is explicit** | Active consensus ingress and gateway checks are dependencies. Typer is not. |
| 12 | **DELETE supported** | No cross-domain consumer found beyond its own CLI/API/wiring. Remove the complete isolated feature; export existing user records only if needed. |
| 13 | **SIMPLIFY; reject logging-only replacement** | Keep or deliberately replace the counter/REQUESTED-review producer before changing the quarantine consumer. |
| 14 | **KEEP during active consensus; revisit later** | It dispatches a budgeted model arbiter and attaches verified evidence, not a human confirmation prompt. B1/B8 and the standing arbiter direction are current dependencies. |
| 15 | **SIMPLIFY proof representation; reject unconditional guard deletion** | Preserve the minimal attempt/target/tier/policy/enforcement validation needed by current dispatch. OS identity alone is insufficient. |
| 16 | **SIMPLIFY with a dispatch/artifact contract** | Preserve required digest/path/reservation/recovery behavior or explicitly cut the dependent artifact feature. No technical dependency on item 11. |
| 17 | **SIMPLIFY with item 13 and health storage** | The health call is already synchronous. Preserve MANUAL/SECURITY dominance, pending recovery, and receipt identity, or implement a real shared atomic transaction. |
| 18 | **REJECT as specified** | Already synchronous; current consensus/proposal effects need atomic intent and recovery. Inventory real vs no-op effects and all writers; design a smaller equivalent before removing the shared broker. |

The proposal's KEEP for `proposals`/`consensus` is correct as an in-flight ownership boundary, not as a declaration that their current implementation must survive unchanged. Its KEEP for health/quota/live telemetry is also correct. The arbiter characterization is false: [review()](../../peerhub/application/arbiter_review.py#L559) detects configured dissent/high risk, reserves budget, invokes a READ_ONLY model dispatch ([line 636](../../peerhub/application/arbiter_review.py#L636)), stores evidence, and attaches a verified opinion ([line 751](../../peerhub/application/arbiter_review.py#L751)). `typer.confirm()` has different semantics and would introduce blocking interaction into this non-interactive workflow.

**Measured validation [empirical_probe]**

| Check | Result |
|---|---|
| Integration CLI directory; thread-new; health quickwins; feedback/error CLI; quarantine integration/policy; arbiter integration; statusline unit tests | **158 passed, 1 deselected**, 151.01s |
| Completion, capability, materializer, live-diag units; operational-error/feedback/proposal/room-gateway integration; effect claim/completion/governance persistence | **188 passed**, 75.28s |
| Six selected real dispatch-workflow artifact/capability/policy-revalidation cases | **6 passed, 15 deselected**, 12.52s |
| Active B1 event-contract file, separately | **35 failed**, 6.08s; all at the deliberate RED-phase `NotImplementedError` |
| Actual parser inventory and statusline invocation probes | 30 root commands; ag exit 0; cc/cx exit 2 with explicit diagnostic |
| Quarantine failure-boundary probes | **2/2 passed** after seeding required health projection |

Total existing tests: **352 passed**, plus **35 separately identified expected RED failures**. This is a focused review, not a full-suite green claim. Pytest's default `not slow and not e2e` selection remained active; no real model asks were dispatched.

The initial standard pytest run encountered Windows access denial in fixture directories created with mode `0700`. The review-only launcher changes that directory-creation mode to `0777` (normal inherited ACLs) inside the test process; it does not patch application logic or assertions. Do not count the aborted setup-error run as product failures or as passed verification. The first custom quarantine probe lacked the health projection needed by the real service; the corrected probe seeds the same projection used by the existing tests.

Reproduction helpers are under `../../../../_sys/data/temp/ask_ask-cc2e/`: `run_review_tests.py`, `quarantine_review_probe.py`, and `quarantine-probe-results.json`. Test fixtures use separate `r2-inherited-*` directories. The quarantine script creates uniquely named isolated databases on each run.

**Required next revision:** correct the inventory and already-completed items; keep the supported deletions small; add the missing completion-contract decision; distinguish named abstractions from the minimal invariants their current callers need; and replace the staging table with one dependency order that protects consensus ingress/effects and the operational-error-to-quarantine pipeline. Retain argparse unless a concrete simplification gain justifies changing it. Do not build replacement architecture for deleted features.

**Final vote: REJECT. A corrected Round 3 is required before implementation.**
