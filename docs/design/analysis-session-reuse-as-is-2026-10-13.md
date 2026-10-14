Analysis document, 2026-10-13; checkout inspected 2026-10-14. Reference, not an instruction file.

# Session reuse: AS-IS and reconciled implementation sequence

Source HEAD: `02f262c1de2417686409879ae7257fd28ea9634f`. Existing uncommitted
plan/index changes were present and are preserved. Source anchors refer to this
worktree; this is a bounded inspection, not a production-store or legacy audit.

This analysis covers the current checkout, the [reuse plan](plan-session-reuse-2026-10-13.md)
and the [session monitoring plan](plan-session-monitoring-2026-10-13.md).
The AS-IS map below is the pre-PR1 inspection snapshot; its anchors refer to
that source revision. PR1 (`eec2cba4`), PR2 (`7cdc6b76`), PR3 (`12129517`), and
health check fixes (`cfb28aba`) are committed on `feat/session-reuse`.
Ordinary asks select compatible retained history with original-result replay,
receipt/counter writers, and explicit `--session`/`--fresh` selectors.
The proposed architecture for inspection, telemetry, and monitoring is specified in the
[Pre-TDD Renewal](peerhub-pre-tdd-renewal-2026-10-14.md) and [Debate Ledger](peerhub-deepthink-debate-2026-10-14.md).

## AS-IS map

Source references below are repository-relative file:line anchors at inspection time.

| Surface | Source | Current behavior |
|---|---|---|
| Public option | `peerhub/cli/app.py:118`, `:192` | `--resume` defaults false and is passed to `ask()` as `resume=args.resume`. No public `--fresh`, `--session`, or session list/show commands. |
| Ask API and runtime construction | `peerhub/extensions/ask.py:25`, `:37`, `:56`, `:61` | `resume: bool = False`; validates CLI peer support, rejects preference options with an explicitly supplied runtime, passes resume to `CliRuntimeTarget`. |
| Prompt identity metadata | `peerhub/extensions/ask.py:96` | Metadata contains runtime kind and binding; it currently contains neither `resume` nor `session_selector`. PR1 must not introduce a preference key. Existing immutable records remain audit evidence. |
| Capability poisoning | `peerhub/extensions/adapters/base.py:803`, `:828`, `:894`, `:915` | Constructor assigns `self.resumable = resume`; advertised capability and `resume_session()` eligibility depend on the caller flag. Fresh calls therefore persist false capability. |
| Vendor invocation | `peerhub/extensions/adapters/base.py:634`, `:683`, `:728`, `:963` | cc uses vendor `--resume`, cx uses `exec resume`, ag uses `--conversation` once the runtime has a vendor ID. These native arguments are adapter details and must remain. |
| Single-slot persistence | `peerhub/extensions/bridge.py:168`, `:608` | `bridge_sessions` primary key is `(stream_id, peer_id)`; `INSERT OR REPLACE` replaces that pair's mapping, storing `runtime.resumable`. Old generations have delivery/event evidence, but no retained selectable registry. |
| Resolution | `peerhub/extensions/bridge.py:531` | Reads current mapping, checks fingerprint, binding, persisted capability, state and workspace lineage; attempts runtime resume; otherwise creates a new generation. Native delta overflow currently also leads to fresh creation. |
| Catch-up | `peerhub/extensions/bridge.py:654`, `:664`, `:686` | Native delta uses the mapping watermark, excludes all same-peer response records, and filters redirects by target. Fresh generation gets bounded catch-up. This exclusion is unsafe when later adding selection of older sessions. |
| Completion watermark | `peerhub/extensions/bridge.py:1082`, `:1100`, `:1178` | Completion binds vendor ID/usage and advances to delivered prompt position; response append then advances watermark to response position. A concurrent intervening record can be skipped next turn. |
| Delivery output | `peerhub/extensions/bridge.py:989`, `:1124`; `peerhub/extensions/ask.py:118` | Exposes generation, effective mode, fallback reason, injected IDs and available usage; no public opaque session identity or monitoring projection. |
| Diagnostic reader | `peerhub/extensions/diag.py:72`, `:256` | `ReadonlyDiag.render(sections, read_at)` produces the diagnostic report. Session inspection must extend its pinned read-only transaction. |
| Paid recall canary | `tests/communication/live/ask_support.py:12`; `tests/communication/live/test_ask_measurements.py:46` | Both first and second turns currently request resume; the first turn therefore persists true capability. This can pass while the normal fresh-first workflow is broken. |

Current flow: CLI flag -> ask keyword -> runtime `resumable` -> persisted
mapping -> Bridge compatibility check -> runtime vendor binding -> native
arguments -> durable completion -> response-position watermark. Bridge already
has claim fencing, an invocation marker (`bridge.py:392`), forward-only certainty
(`:34`), replay and stranded-delivery recovery (`:1366`). Extend these contracts;
do not replace them with a new session execution ledger.

The `resumable=0` defect is supported by the code. The reuse plan's claim about
171 production sessions and token savings is historical external evidence,
not re-measured by this checkout analysis.

## Gaps against both plans

| Requirement | AS-IS gap | Implementation boundary |
|---|---|---|
| Delete public resume preference | CLI, ask signature, runtime construction and capability still depend on it | PR1 removes the entire public/caller path and documents default current-slot reuse. |
| Independent resume capability | `resumable` is caller intent | PR1 uses provider capability; native vendor resume stays intact. |
| Retained identity / generations / retention | One mutable mapping, no handles or history counters | PR2 changes schema and every runtime consumer together; no pointer-only intermediate merge. |
| Default latest compatible / exact / fresh | Only current mapping; no selector API | Add final selector contract only when history, replay, queued failures and catch-up are implemented. |
| Consumed-context watermark | Response position may cover unseen records | Correct in PR1 before making reuse default; conservatively invalidate unprovable old mappings. |
| Older-session catch-up | Same-peer responses omitted | PR2 includes intervening peer responses; PR3 adds exact addressing on that working registry. |
| Stable replay and certainty | Existing request replay and fencing exist, but no selector/handle snapshot | Extend immutable identity and terminal result together; never retry after possible execution. |
| Session monitoring evidence | No handle-attributed call receipts, counters or metadata projection | Add writer schema and STARTED/terminal writers with registry; unknown historical values stay null. |
| Inspection and telemetry | No list/show or session snapshot API; usage is not context occupancy | Add read-only readers and attributable final-request telemetry; unavailable metrics remain unknown. |
| Monitor views / alerts / export | Session UI and shared alert policy are proposed | Build on committed writer evidence and existing read-only diagnostics; no second collector. |

## Proposed interfaces that do not exist yet

Targeted searches of `peerhub`, `tests` and `tools` found no implementations of
the proposed names below. Tests named in the plans are also proposed unless
identified in the AS-IS map.

| Plan citation | Reality and reconciliation |
|---|---|
| `ProviderSpec.can_resume`, `CliRuntimeTarget.can_resume` | New static capability properties. Keep Bridge's existing `RuntimeTarget.resumable` protocol as the capability boundary; do not require custom runtimes to implement a second property. |
| `peerhub/core/identifiers.py`, `generate_session_handle()` | This module does not exist. Put the handle generator in the Bridge extension when PR2 first persists handles. Core owns no session identity. |
| `bridge_session_history`, `bridge_session_counters`, pointer columns | New extension schema; existing `bridge_sessions` is the full mapping, not a pointer. |
| `resolve_session_by_handle()`, `SessionBridgeError` and selector exception subclasses; CycleResult handle/mode/reason fields | Proposed additions; current `SessionError`, generation and detail fields do not implement them. Resolver writes require the existing claim fence. |
| `bridge_retention.py`, `session_telemetry.py` | Proposed extension modules; register classifications/import permissions and continuity module lists when introduced. |
| `ReadonlyDiag.snapshot()` | Non-existent API. Extend `ReadonlyDiag.render(sections, read_at)` and share a session reader on its already pinned read-only connection. |
| Reuse plan's pointer-only `_read_sessions_section()` sketch | Proposed and insufficient: it omits older retained sessions and projection metrics and scans without pagination. Use the monitoring plan's bounded readers. |
| `_native_delta(...) -> ProjectedRecords` in reuse interface sketch | Method exists, but returns `CatchUpProjection`; `ProjectedRecords` is a separate existing catch-up type. Preserve the actual return contract. |
| `session_monitoring.py`; `read_session`, `read_session_page`, `read_peer_sessions`, `read_latest_quota`; snapshot/page/aggregate types | Proposed reader module/interfaces in monitoring section 3, not current diagnostic APIs. |
| `session_call`, `session_telemetry`; stats/scope projections and indexes | Proposed writer receipts and materialized counters. Current deliveries/evidence have no handle/mode/token columns; `result_json` and JSON evidence detail carry additions. |
| `--fresh`, `--session`, `session list/show`, session pane/line/alerts/export | Proposed public surfaces. Do not advertise them before their complete implementation PR. |

The long reuse-plan code blocks are design sketches, not patches ready to copy.
For example, expiry code using an unfenced transaction and pointer-only inspection
does not satisfy the final writer/reader contracts. Reuse section 8 is reconciled
with those contracts; monitoring section 3 controls inspection bounds and types.

## One reconciled PR sequence

This is the canonical session sequence for both plans. PR numbers below refer
to this sequence; the separate MV2 quota/infrastructure plan is a prerequisite
where stated, not a second session implementation sequence. Each PR merges only
after its own deterministic checks pass, uses final contracts immediately, and
introduces no disabled selector, unused handle generator, compatibility alias,
placeholder writer, dual session store or code scheduled for deletion later.
Monitoring schema/policy questions remain design decisions, not implementation
approval granted by this analysis.

### PR1 — Delete caller resume preference and make current-slot reuse safe

- Delete PeerHub `--resume`, `args.resume`, the `ask(resume=...)` parameter,
  validation/error wording and every runtime construction/kwargs/helper path
  forwarding a caller resume preference. Delete it from tests and paid-canary
  argument helpers in the same PR. Python rejects the removed keyword and CLI
  rejects the removed option; no alias, deprecation path or preference shim.
- Set `ProviderSpec.can_resume` for cx/cc/ag independently of any call. The CLI
  runtime's `can_resume` and existing `resumable` reflect that capability;
  Bridge persists `runtime.resumable` so custom runtimes retain their protocol.
  Keep `resume_session()`, its Bridge-supplied `vendor_session_id`, vendor resume
  arguments, local vendor-binding cache, capability probes and `control.resume`.
- Do not write a caller `resume` preference to prompt/response metadata,
  runtime binding or durable request identity. Current prompt metadata has no
  such key; remove any other discovered writer/caller path rather than adding
  one. Never rewrite historical immutable records to erase audit evidence.
- Before activating default reuse, retain only the last consumed **prompt**
  position as watermark. Stop advancing it to appended response position.
  Include later records conservatively, including same-peer responses whose
  exclusion cannot be proved by session-specific evidence. Prove concurrent
  prompt 5 / record 6 / response 7 yields next delta containing record 6.
- In a writer-owned, idempotent upgrade, correct known CLI capability values
  without treating capability as continuity proof. Reconstruct old consumed
  watermarks only from complete exact delivery/context evidence; otherwise
  mark native mapping LOST and clear continuity. Preserve custom local resolution
  via `resume_session(external_session_id)` without vendor ID/native watermark,
  and delivery-evidence catch-up (`bridge.py:559`, `:654`;
  `tests/communication/bridge/test_native_resume.py:295`). Preserve custom-runtime capability,
  lineage, fencing, terminal replay and uncertain blocking. Registry migration
  later reuses this permanent validation rule.
- Update all shipped public surfaces now: parser description/help, README,
  CONTRIBUTING, adapter guides/reference, doc map/current status where relevant,
  current specification recipes, command inventory expectations and CHANGELOG.
  Record the breaking removal in the new changelog entry; revise historical
  public recipes claiming caller opt-in, retaining factual vendor/release history.
  Design and historical analysis may name the removed flag to describe removal,
  but must not instruct callers to use it. If `docs/m1_spec` is edited, regenerate
  and include its seal. These edits are future PR1 scope, not this docs task.
- User-visible behavior after this PR: ordinary asks reuse the compatible
  current mapping; otherwise fresh with bounded catch-up and existing reason
  output. Describe only the shipped current-slot scope. A separate task uses a
  dedicated stream. Do not advertise retained selectors or inspection yet.

Gate: adapter/Bridge/CLI/replay tests, emitted help bytes, removed-keyword and
removed-option rejection, false-capability upgrade, concurrent watermark and
uncertain-execution regressions. Update the recall canary to a normal first ask
and normal continuing ask; first-call default must persist resumable capability.
The corrected prompt watermark may conservatively inject the previous response.
Replace the old `injected_record_ids == []` assertion with proof that the original
token-bearing prompt was not injected; mode/native-identity evidence must also
prove continuation. Do not restore a response-position watermark to satisfy a test.

### PR2 — Retain sessions and switch all writers/readers atomically

- Add extension-owned handles, history, monotonic counters, MRU pointer,
  retention/expiry and eligible pointer repair. Mint handles only when persisted;
  put the generator in the extension. The cap is 20 non-tombstoned retained
  sessions per scope; tombstones are retained/paginated audit rows, not a claim
  of a total-table bound.
- Migrate the old slot and convert every mapping consumer in the same PR:
  resolution, create/resume, catch-up, completion, response append, cancellation,
  taint/loss, control effects, terminal recovery, boundary validation and existing
  diagnostics that query the old schema. No intermediate pointer table with
  code still expecting vendor ID/binding/watermark columns on it.
- Default resolution searches retained eligible history for the latest
  compatible session, rather than only the MRU pointer. Keep the PR1 watermark
  rule and complete intervening-record delta; overflow fallback is allowed only
  when non-execution is proven. Resume, pointer and history updates remain fenced.
- Persist final normalized `session_selector="latest"` request intent and
  immutable handle/mode/reason/result snapshots now. Request-id replay returns
  the original result without reading current pointer/liveness or starting a
  provider; complete scoped identity is compared first. Selector-less historical
  terminals remain inspectable, without a replay shim. Consume definitively
  unstarted legacy pending requests with `legacy_selector_unsupported`; uncertain
  requests remain blocked. Do not reinterpret old queued intent as new default.
- Install monitoring schema/indexes/projections with working writers now:
  exactly one call receipt at confirmed STARTED; exactly one terminal telemetry
  receipt with final schema and null unknowns until attributable measurements
  exist; receipt insertion/counter updates in the same fenced transaction.
  Preserve unknown legacy counts and scope coverage. Backfill never invents
  calls or appends old calls with new sequence ordering. Include the bounded
  quota-read index migration required by the monitoring reader.
- Expose the final session envelope/trailer for shipped default asks, nullable
  error session and unknown telemetry, with no promises of measured occupancy.
  Update output docs and package/CLI tests immediately. This is permanent output,
  not a temporary alternate schema. Register any new extension modules in both gates.

Gate: atomic migration/rollback/reopen, retention and monotonically allocated
generations, compatible history selection, loss/taint/control/recovery consumers,
original-result replay, exactly-once receipts, incomplete legacy counts, final
stdout/JSON bytes and no auto-execution after STARTED/MAY_HAVE_STARTED.

### PR3 — Add fresh/exact selection end to end

- Add `ask(fresh=..., session=...)`, mutually exclusive CLI `--fresh` /
  `--session HANDLE`, normalized durable selector intent and complete exceptions
  together, using only the Python keyword `session` (no `session_handle` alias).
  Capability stays static; no caller resume preference returns.
- Enforce scope, lineage, fingerprint, effective binding and expiry; native
  sessions also require vendor ID and trusted watermark. Preserve the custom
  local-session branch described in PR1. Explicit selection never silently chooses another session;
  `--fresh` still receives bounded Stream catch-up.
- Validate direct new calls and overflow before prompt append where provable;
  queued intent is revalidated under the claim fence. Definitive NOT_STARTED
  selector failures commit result/evidence/offset consumption together. Storage
  faults, lost fences and unresolved invocation markers do not qualify.
- Check immutable request identity before cached replay, then return its original
  session snapshot before handle liveness checks. Reusing an older handle includes
  all intervening eligible records, including other sessions' same-peer responses.
  Existing call/telemetry receipts describe the session actually executed.
- Update help, recipes, changelog, deterministic tests and live helpers in this
  PR; use existing mode/reason/session output contracts rather than another format.

Gate: default/fresh/exact public CLI and Python workflows, every mismatch/error
and mutual exclusion, older-session delta, expiry commit before rejection,
queued rejection progress, replay after retention/expiry and changed-selector
conflicts. Failure must neither spawn nor allocate a substitute exact session.

### PR4 — Add attributable telemetry and bounded session inspection

- Prerequisite: the final shared MV2 snapshot/policy reader baseline. The
  monitoring reader signatures take that policy, so this dependency applies to
  PR4, not just the later UI. PR1-PR3 can land without it; do not create a temporary
  session-only policy adapter while waiting for the shared baseline.
- Introduce `session_telemetry.py` with provider-bound final-model-request
  occupancy, reported context-window provenance and cached quota age. Billing
  totals and cumulative usage are not occupancy. Unknown cc/ag windows remain
  null until measured; never fabricate percentages. Future terminal receipts
  record measured data atomically; immutable past results remain unchanged.
- Introduce `session_monitoring.py` readers from monitoring section 3 on an
  already pinned read-only connection. Extend `ReadonlyDiag.render`, and ship
  session list/show with paging/filtering and exact full-handle selection in the
  same PR. Share readers, schema/null rules and cached quota interpretation;
  readers never migrate, prune, create stores, acquire claims or call providers.
- Implement final threshold stderr advisories, retained identity/binding views,
  completeness/coverage, strict staleness and privacy allowlists. Update docs,
  inventory and both extension module gates. TC-SR-77 and the monitoring reader
  catalog test the same implementation rather than duplicate surfaces.

Gate: attributable context extraction, cache/tool-loop/reset cases, null and
stale rules, receipt/result agreement, bounded indexed queries, pagination,
missing-schema capabilities, privacy, read-only/no-provider/no-store-creation
proof and exact emitted text/JSON. This PR provides the session snapshot API.

### PR5 — Integrate sessions into the shared monitor and diagnostic policy

- Build session line/pane/alerts/local acknowledgement/export on PR4 snapshots
  and the MV2 snapshot, policy, colour, watch and export infrastructure. The MV2
  features are explicit prerequisites; do not introduce a second collector or
  disposable watch loop. Quota-only MV2 work can land independently beforehand.
- Implement the session portions formerly assigned separately to MV2 PR2/4/5/6/7
  in this one session integration PR: coverage-labelled selected-stream metrics,
  alert hysteresis/exit precedence, local ack, NDJSON allowlist and user docs.
  Collection remains explicit monitor behavior; the session reader is always
  read-only and `diag --live` never collects; `monitor` composes collection with
  the same read-only snapshot.
- Keep unknown measurements visible; no telemetry-driven switching. Close the
  monitoring proposal's policy/schema decisions before implementing those parts;
  this document does not silently approve them. A runtime read of an unmigrated
  store reports unavailable/partial, never performs a repair.

Gate: shared policy/exit and alert boundaries, stdin/watch/ack lifecycle, bounded
refresh/no extra collection, byte/schema/privacy tests and agreed performance
benchmark. Reuse does not depend on this optional monitor UI for safe execution.

For **every implementation PR**, run `python -m pytest -q`, `pyright`,
`python tools/command_inventory.py --check`, and both module gates for new
extensions. Update relevant tests, docs, help and changelog in that PR. Run the
seal tool whenever frozen-package documentation is edited. A version bump,
candidate-matched CI/package/live evidence and release remain a separate normal
release process; no PR gets a green claim from later work or a live-test skip.

## Live-check plan (explicit opt-in; not executed here)

Deterministic fake-provider tests establish selection, exact argv, negative
paths, replay, concurrent catch-up, counters and crash/cancellation semantics
first. A recalled word alone does not prove native reuse: require accepted mode,
unchanged native identity in sanitized evidence, generation/handle as available,
and injected record IDs. Never print raw vendor IDs or credentials in reports.

Use a normally created scratch workspace/store under `.peerhub/work/`, explicit
global `--db`, unique stream/request IDs and standard profiles reviewed for local
overrides. Record source SHA **and dirty-worktree status**, OS/Python/provider CLI
versions, effective binding, sanitized stdout/stderr, certainty, generation/handle,
mode/reason, injected IDs and receipt deltas. Keep timeout 240s, output <=65536
bytes and prompts tiny. No retries, quota refresh, fault injection or writes to
the production store. Providers require authentication and opt-in; CI refuses
live work. Missing provider skips are unavailable evidence, not passes.

| Stage | Bounded real calls per provider | What must be observed |
|---|---|---|
| PR1 | 2 | First normal ask stores independent true capability; second normal ask recalls a random token with resumed mode and same generation/native digest. The original token-bearing prompt is absent from injection; a known prior response may appear beyond the corrected prompt watermark. Public removed-option rejection is tested offline. |
| PR2 | 2 additional only if checking that PR candidate | New normal turn then continuation: same retained handle, final session output, one fresh and one resumed STARTED receipt; replay the request offline with a fail-on-spawn harness and verify unchanged receipt counts. |
| PR3/final candidate | 4 | Default creates A and remembers token; default reuses A; fresh creates B with a benign marker; exact A recalls and includes B's committed intervening context. Check distinct handles, same A generation on reuse, accepted modes and injected IDs. |
| PR4/5 | 0 additional | Inspect committed candidate receipts with session list/show and read-only `diag --live`. `monitor` shares the same snapshot but also collects quota. Require attributable numeric telemetry or explicit unknown values, read-only store/no probes for inspection, correct counts/coverage and sanitized export. |

A final integrated four-turn run replaces the two-turn PR1/PR2 checks when only
the final candidate needs verification; those incremental runs are optional,
not prerequisites to spending more quota. Budget: four asks/provider, 12 across
cx/cc/ag for the final session suite; at most eight/provider, 24 across all three
if both incremental candidates are also checked. Baseline writable/cancellation
canaries and release live stages have separate budgets; do not add them silently.
No optional performance/probe call is hidden inside these budgets.

For the existing optional canary harness after PR1 updates its flag/helper tests:

```powershell
python -m tools.live_gate --yes --only canary --peer cx
```

That command runs the broader existing canary selection (up to five asks for one
peer), not exactly the four-turn session matrix. To bound the PR1 recall-only run:

```powershell
$env:PEERHUB_LIVE = "1"
python -m pytest -q -m "canary and cx" tests/communication/live/test_ask_measurements.py::test_resume_recalls_first_turn_token
```

PR3 adds `test_fresh_exact_four_turn_session_matrix` as an explicit opt-in
canary with `PEERHUB_SESSION_SELECTION_LIVE=1` in addition to `PEERHUB_LIVE=1`.
Its [documented invocation/budget](../../CONTRIBUTING.md#local-live-gate) is
four asks per selected provider. The existing recall test alone does not cover
exact handles or monitoring. Timeout/uncertainty
stops the sequence and requires reconciliation; do not rerun automatically.
Optional canary artifacts are not release evidence. Full release validation
follows CONTRIBUTING against the exact clean candidate and all required stages.

## Completion boundary

AS-IS anchors, the cross-plan gaps, proposed-interface corrections, one complete
PR sequence and a bounded live-check plan are sufficient for this task. No
exhaustive trace, provider verification, implementation, release or commit is
included. The companion plans link to this sequence instead of maintaining
conflicting PR orders.
