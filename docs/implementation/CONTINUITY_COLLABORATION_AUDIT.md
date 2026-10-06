# Continuity and collaboration implementation audit

Date: 2026-10-06. This is a working-tree audit, not an exact-head promotion or release attestation.

## Authority and scope

The ToGo `START_HERE_KO.md`, mandatory M2/M3 roadmaps and detailed pre-TDD reviews are authoritative. `docs/m2/*CONTRACT.md` and `docs/m3/*CONTRACT.md` describe concrete ports and invariants. Historical milestone import names are not a reason to restore removed runtime packages. Formal frozen maturity being PLANNED does not establish that implementations are absent: all seven continuity and seven collaboration slices have modules and deterministic tests.

Review focused on recovery, concurrency and public integration rather than reconstructing archived v0 machinery. No live provider calls were made. The v0 archive is a behavioral reference, not authority to reintroduce coordinator, general shell, consensus or hidden orchestration state.

## Fixes made during this audit

### Approval single use and lifecycle races

`peerhub/extensions/approval.py`, `ApprovalEngine.consume_approval` formerly performed separate reads and an unconditional update. Independent consumers could both accept one token. Consumption now uses SQLite `BEGIN IMMEDIATE`, validates exact effect and expiry within the transaction, and commits one state transition. Approve/reject use compare-and-set from REQUESTED; stale expiry cannot overwrite terminal states.

`tests/collaboration/test_approval.py` adds an independent-engine synchronized race and stale approve/reject/expiry regressions. These are stronger evidence than the existing serial APP-005 double-consume test. This proves token consumption; atomic coordination with a downstream external effect still belongs to its caller's execution-certainty boundary.

### Orchestration attempt budgets and late success

`peerhub/extensions/orchestration.py`, `PlanExecutor._run` formerly checked `max_steps` outside the retry loop. A single step with `max_steps=1` executed three attempts. The final synchronous step also returned COMPLETED after exceeding its deadline because time was only checked before subsequent calls. Both were reproduced directly before editing.

Attempts now count against the total step budget, including recorded attempts on resume. Resume preserves attempt numbers and cannot reset retry allowances. Final-step deadline overruns are rejected. Invalid bounds, duplicate/missing dependency IDs and mismatched runner results are rejected before becoming successful execution evidence.

The runner remains synchronous: overrun detection is **not hard cancellation**. A callback blocked forever cannot be safely killed by a thread wrapper, and returning early while its effect continues would falsely imply bounded execution. This remains an explicit promotion blocker requiring a bounded runtime adapter.

Verification: `python -m pytest tests/collaboration/test_orchestration.py tests/collaboration/test_approval.py -q`: **38 passed**. Targeted Pyright: **0 errors, 0 warnings**. These are local working-tree results, not the final full suite or CI matrix.

### Routing and runtime port validation

`routing.py` now uses direct ascending Unicode peer-ID sorting after descending eligibility/score, fixing both non-Latin crashes and prefix tie ordering. Omitted health is UNKNOWN; REJECT disqualifies unknown health/quota, while DEGRADE explicitly allows unknowns without invented quota headroom. Invalid policy modes, revisions, non-finite/out-of-range scores, invalid quotas and duplicate peer IDs fail closed.

`runtime_port.py` validates the existing parameter-map schema before invocation: supported JSON scalar/container types, optional numeric/size/enum constraints, finite JSON values and no undeclared arguments. Unsupported constraints fail registration rather than silently pass. Capability and invocation values are defensive snapshots. FAILED/CANCELLED/COMPLETED remain terminal under cancel. Trusted handlers remain deliberately **not a security sandbox**, and synchronous loopback does not claim hard cancellation.

Verification: Routing/Runtime suites **46 passed**, targeted Pyright **0 errors, 0 warnings**. Regression coverage includes Unicode/prefix IDs, UNKNOWN defaults, invalid numerical evidence, pre-invocation type/bound rejection, caller mutation and terminal evidence preservation.

### Exact routing evidence and optional durable plan journal

Routing decisions are now frozen, including tuple/frozen evaluation breakdowns. `input_snapshot_json` preserves complete canonical request, policy and candidate values; optional capability/health/quota/eval source refs are retained, with missing refs explicitly null (UNKNOWN provenance). `input_digest` is bound into the decision digest. `Router.replay_decision` verifies both input and output and selects from the original snapshot, never current provider measurements. An unreferenced scalar remains unverified input; this does not fabricate a Catalog/Observation source or write authoritative routing truth.

`RecordPlanJournal` optionally binds `PlanExecutor` to public Core Record ports in a dedicated single-author Stream. Explicit `accept_plan` records immutable Plan JSON/digest, accepting authority, optional Artifact/approval evidence refs and an absolute deadline that survives restart. Evidence ref verification is the accepting caller's responsibility. Plan itself is stored authoritatively as a Record; an Artifact copy is optional. Cost accounting is explicitly NOT_CONFIGURED, not measured zero or unlimited.

Every callback invocation commits RUNNING/MAY_HAVE_STARTED evidence first, with a unique owner and per-attempt idempotency binding. The public append guard checks single-author ownership inside the Core write transaction. Concurrent executors cannot both invoke one attempt. Callback exceptions leave the committed uncertainty intact; durable recovery refuses blind replay even if a step merely declares itself idempotent. Terminal outcomes require the matching committed owner. Resume trusts authoritative records, not fabricated caller dictionaries; accepted Plan payloads are defensive snapshots; reads page to completion. Artifact and Core domain topology are unchanged.

`require_hard_deadline=True` explicitly fails closed with `UnsupportedExecutionBoundError`, because no hard-cancellable runtime binding exists yet. The existing callable/test API and optional journal's best-effort mode **do not claim production hard-bounded execution**. Parallel/conditional orchestration, verified cost bounds and explicit external reconciliation adapters remain future contract closure work.

Verification: combined Routing/Orchestration/Approval/Runtime suites **95 passed**; targeted Routing/Orchestration Pyright **0 errors, 0 warnings**. Added exact replay/tamper/input-mutation, pre-callback durability, crash uncertainty, independent concurrent execution, absolute-deadline restart, caller-evidence rejection, paged replay and strict-mode rejection regressions.

## Remaining findings at audit snapshot

Line numbers below refer to the pre-fix snapshot for modules owned by other workstreams; use named functions if subsequent edits move lines. Parent integration may resolve findings after this audit.

| Priority | Finding and source | Required closure |
|---|---|---|
| High | Backup `restore_authoritative`, `backup.py:275-277`, copies directly into an existing live target after staging, merges artifacts, and leaves old DB sidecars. A mid-copy failure can leave mixed generations. | Verified complete staging, recoverable atomic publication under an explicit offline/exclusive restore boundary; inject failure during publication and prove old state retained. |
| High | Backup `create_backup`, `backup.py:124,184`, changes `secret_exclusion_policy` based only on a boolean; secret guard is never invoked on copied DB/blobs. | Fail closed on prohibited plaintext before publishing READY; preserve CAS integrity instead of silently modifying digest-addressed blobs; prove secret-bearing input fails. |
| High | Backup generation is only a returned integer (`backup.py:261`); no durable fence or stale-writer enforcement exists in CoreStore. | Explicit durable fence and ownership/reconcile protocol; demonstrate a pre-restore writer cannot commit after restoration, without expanding Core domain topology. |
| High | Work recovery calls `core_store.read_records(stream_id)` in `rebuild_derived_projections`, while Core's default limit is 100 (`core/store.py:413`). | Page every authoritative stream to completion; test a relevant transition after record 100. |
| High | Memory `rebuild_from_records`, `memory.py:338-351`, turns every `m3.memory.*` record into a fresh random-ID ACCEPTED item, including reject/revoke/supersession evidence. Normal memory mutations only update the projection. | Define and replay typed lifecycle evidence preserving IDs, revisions, timestamps and source refs; prove accepted/rejected/revoked/superseded logical equivalence after projection deletion. |
| High | Search `rebuild_from_sources`, `search.py:304-327`, ignores supplied Artifact/Skill ports and indexes at most 10000 records per stream. Memory has the same fixed ceiling (`memory.py:335`). | Public-port source enumeration, paged complete replay, and rebuild equivalence for mixed source types and late records. |
| High | Memory `_estimate_tokens`, `memory.py:67-69`, counts whitespace words: arbitrary unspaced Unicode can cost one alleged token. | A documented conservative byte budget or provider-specific tokenizer; include multilingual and long unspaced inputs plus envelope overhead in bound tests. |
| High, partially fixed | Optional RecordPlanJournal now binds accepted Plan/digest, persists pre-invocation certainty, enforces attempt ownership, rejects fabricated caller recovery and preserves absolute deadline across restarts. | A real hard-cancellable runtime binding, verified cost bounds and explicit external reconciliation still require closure. Strict hard-deadline execution fails closed unsupported; do not promote best-effort callable execution as production-bounded. |
| High | MCP `_list_tools`/`_call_tool`, `mcp.py:208-300`, expose only two Core tools; constructor Work/Artifact/Skill ports are unused (`:88-90`). No initialize-driven usable session handle; shutdown absent. | Connect required stable public ports and explicit session lifecycle; deterministic protocol client integration, error isolation and idempotent retries. ToGo marks MCP Tasks/Prompts baseline N_A, so extra prompt features are not a required blocker. |
| Medium | MCP resources query private `CoreStore._read` SQL (`mcp.py:305`); tools/call returns plain business dictionaries instead of MCP `content`; null-ID request is treated as notification; error strings can leak paths/secrets. | Public enumeration port; canonical protocol framing/shape; explicit ID presence validation; sanitize errors and content through one boundary. |
| Medium | Backup excludes registered skill sources despite required authoritative recovery class; `create_backup` has no skill-source port/manifest entry. Existing target directories can retain stale files. | Add explicit source inventory/reference policy and complete recovery proof; avoid implying a Core+CAS-only bundle is full recovery. |
| Fixed locally | Routing tie-break formerly used `chr(255-ord(c))`, crashing on non-Latin IDs and mishandling prefix ties. | Direct Unicode sorting and independent order-reversal regression now pass; final exact-head CI remains required. |
| Fixed locally | Routing now retains immutable complete input snapshot, source refs/null UNKNOWN provenance, input digest and output binding; replay verifies all captured evidence. | Upstream callers must supply genuine Catalog/Observation/Eval refs when available; persistence is their public Record/Artifact responsibility, never fabricated by select-only Router. Final integration/evidence gate remains required. |
| Fixed locally | Runtime formerly checked presence only and changed FAILED to CANCELLED; mutable nested values could change captured invocation evidence. | Typed constrained parameter-map validation, defensive snapshots and terminal-preserving cancel now pass. Handlers are explicitly trusted; live clusters/daemons are **not** mandatory M3 work. |
| Medium | A2A dispatch only builds an in-memory SUBMITTED response; no bound external transport; update accepts arbitrary nonterminal target statuses and duplicate task IDs overwrite task state. | One bounded adapter binding, strict state/identity/evidence transitions and duplicate conflict/reconciliation tests. Do not conflate independent agents with execution workers. |

## Tests that are weaker than their names

- BCK-009 asserts the manifest label and invokes the detector separately; it never places a secret in the backed-up source.
- BCK-010 corrupts the manifest before restore; it does not inject a mid-restore failure or test rollback over existing state.
- BCK-004 checks the returned generation integer; it does not test a stale writer.
- ORC-004 uses two steps, so the next step's preflight detects lateness; a final-step overrun was untested until this audit.
- APP-005 exercises serial reuse, not independent concurrent consumption; now supplemented.
- Search/Memory rebuild tests do not establish complete mixed-source or lifecycle equivalence.

## Promotion gates

Do not promote M2/M3 from module presence or test counts. First close exact-candidate M1 public-cutover evidence, then M2 entry/exit with all continuity extensions disabled and authoritative rebuild equivalence; then M3 entry/exit with collaboration disabled, exact decision provenance, supersession/revocation replay, bounded crash recovery and single-use approval. Preserve honest unresolved contracts rather than relaxing frozen requirements to fit a test suite. CI matrix/live/package evidence must match the final committed candidate.
