# PeerHub recursive MECE / token-efficiency review — 2026-10-20

Status: **evidence-based review, NOT a proof of universal correctness**. Baseline: `main@1c99c1346d92eba3244f7fc25a8d6da70071ab4c`, published v0.21.5, CI push run 39394855461 succeeded. The review branch is non-release and must not trigger paid provider calls. `docs/STATUS.md` owns release state; PR #200 separately proposes a status update.

## 1. Scope and evidence standard

Review vertical chains (input → adapter → bridge → persistence → read model → view → action) and horizontal contracts (all cc/cx/ag, all pools, all statuses, concurrency, OS, session and migration scenarios). Evidence levels:
- **Confirmed by current source:** exact code-path observation or deterministic arithmetic; not necessarily reproduced end-to-end.
- **Observed externally:** issue/release evidence, with version and provenance; do not project old observations onto the current build.
- **Unverified / design gap:** needs an executable fixture, real provider permission, or new acceptance contract.

Do not equate passing CI with provider compatibility, cost saving, real-world forecast calibration, or complete peer balancing.

## 2. Architecture / MECE ownership map

| Layer (single authoritative owner) | Invariant / output | Read/feedback consumer | Gap / control |
| --- | --- | --- | --- |
| Configuration + CLI profiles | Explicit model, effort, workspace, override provenance; no hidden charged probe | Adapter / inspect / runtime view | Drift needs a cached capability fact and age, not speculative defaults |
| Adapter cc/cx/ag | Typed success, provider error, partial output, usage and exact session identity | Bridge, receipts, CLI | Detect status=ERROR even if the CLI process exits 0; test with a captured sanitized fixture |
| Process supervision | Bounded wall/silence time, cancellation and no orphan | Bridge, diag and audit | Treat post-spawn ambiguity as uncertain; do not auto-retry an effectful action |
| Communication Core / Bridge | Durable dedup, claims, single-owner streams, receipts, uncertain recovery | Monitor, replay, routing evidence | Test crash-at-every-transaction-boundary + multi-process races |
| Usage normalization | Provider-native counters preserved; canonical fields never invented | Usage monitor, future cost model | Never sum cumulative session counters as per-turn charges |
| Quota Observation | Sample provenance, freshness, pool identity and unavailable state | Quota projection | An observation isn't a reservation; shared pools must have one canonical capacity |
| Quota projection | Conditional window-average forecast, not a reliable personal spending prediction | Monitor and advisory policy | Backtest on bounded traces, false positive/negative with ground-truth reset |
| Routing | **Selects only**; digest/replay and fail-closed unknown policy | Future dispatch decision | Current cost sensitivity is not used in rank; quota=0 is not rejected |
| Orchestration / approvals | Bounded work, idempotency, explicit action authorization | Process execution and audit | Guarantee budget stop on each expansion/loop and outcome settlement |
| Monitor CLI / TUI | One read snapshot; show uncertainty and staleness; no model calls on redraw | Operator | Interactive TUI remains deferred; display should remain auditable on 40/80/160 columns |
| Testing / release | Deterministic fixtures, Python 3.11–3.14 / Windows+Linux; opt-in provider gates | CI, regression feedback | CI green does not validate currently exhausted provider profile, native terminal, or external quotas |

Flow closure criterion: every decision must have a typed fact, source/time, policy revision, deterministic outcome, and post-execution correction path. No observation must trigger its own paid probe without explicit authorization.

## 3. Confirmed findings and issue disposition

### F-01 — Confirmed display bug (fix included in this branch; low risk)
`peerhub/cli/monitor_view.py:_pct` checked `0 < remaining < 0.005`, then formatted `remaining*100:.0f`. Python ties-to-even formatting turns **exactly remaining=0.005 into `0%`**, although 0% means exhausted. Regression: inputs 0, tiny positive, 0.0049, **0.005**, 0.005000001, 0.01, 1.0. Fix determines the rounded whole-percent string first, then replaces positive rounded-zero with `<1%`. The same helper feeds both quota row and header. Re-run existing responsive-view snapshots.

### F-02 — Confirmed routing intent/effect gap (design-before-change; medium/high)
`peerhub/extensions/routing.py` validates and fingerprints `RouteRequest.cost_sensitivity` but uses only candidate `eval_score` and `quota_remaining/1000` in its score. The numeric parameter cannot change the selection. This is a functional contract gap **if** callers expect cost sensitivity to influence routing; do not silently invent pricing or a new cost unit. Define versioned, provider-specific estimated marginal cost and uncertainty, or rename/deprecate the unused input; then add pairwise rank-flip and no-cost-evidence tests. Router has no inherent execution side effects.

### F-03 — Confirmed zero-headroom routing hazard (design-before-change; high)
A `CandidatePeer` with `quota_remaining=0` remains eligible (the score merely loses a bonus); a higher eval score can win even with no remaining capacity. Fail-closed dispatch must reject a *known exhausted* applicable pool; unknown must stay unknown. Do not conflate token counts, provider percent remaining, requests, 5-hour vs 7-day windows, and reset credits. Add explicit units and source timestamp before connecting the existing Router to live quota facts.

### F-04 — Open issue #199: provider-error visibility (observed on 0.21.3; current version unverified; high)
The user reported about 20 `ag.deepthink` empty-response messages and a `RESOURCE_EXHAUSTED 429` error only in JSON. Main currently handles nonzero process exit, `is_error`/`error` in flat ag JSON, and uncertain `provider_limit` guidance; however `AgSpec.parse` does **not explicitly check a possible flat `status` field**. A provider that exits 0 and returns status=ERROR with empty response and missing/empty `error` could pass that parser. This **specific payload is a hypothesis pending captured flat JSON evidence**, not a claimed reproduction. Add a fake CLI fixture for exit 0 + status ERROR/empty response, exit 3 + stderr, typed error object, 429 with reset, and denial-only content. Never classify an empty response as success based on an exit code alone; preserve uncertainty if tools might have run.

### F-05 — Release/doc authority drift (confirmed; coordination risk)
At time of reading, `docs/STATUS.md` top said "v0.21.5 release candidate" and "latest published v0.21.3", while GitHub release v0.21.5 was published at main. PR #200 addresses this drift, but its proposed `docs/HANDOFF.md` says "no open issues" while issue #199 remains open. Reconcile with PR #200; avoid overlapping edits here.

### F-06 — Historical token/cost data (reported, not causal universal claim)
`docs/implementation/CONTEXT_EFFICIENCY.md` measures warm Claude session reuse vs fresh injection on a fixed example: approximately $0.0037 vs $0.0216 per repeated question, while total model input tokens remain similar. Codex cache hits were variable; Agy resume offered continuity without demonstrated token savings. Cache-price and token savings are separate metrics. `docs/benchmarks/v0.16.0-to-v0.21.2-2026-10-19.md` measures CLI startup and code size, **not** live provider spend; guide output grows to ~1,044 approximate tokens at v0.21.2. Optimize measured repeated context first, not just shorter displayed CLI output.

### F-07 — Monitor capabilities vs interactive TUI (confirmed scope)
`peerhub monitor` already has grouped, responsive snapshots / watch / NDJSON, quota/usage/session data, and collection cadence distinct from redraw. The documented *interactive* keyboard-navigable TUI remains deferred. Avoid building a second telemetry store merely for UI; first prove operator tasks that watch mode cannot serve. Quota probe may have token-cost hazards; default to cached observations and explicit opt-in collection.

## 4. Token saving / processing recommendations (priority)

1. **P0 stop wasted retries:** provider-error-aware dispatch circuit breaker keyed to precise `provider + account pool + model + reset`; do not loop on exhausted pool or a result marked uncertain. Count rejected/ambiguous attempts separately from successful tasks. Never auto-switch to a different pool to evade provider limits.
2. **P0 correct evidence:** retain native usage + provenance, normalize `input`, `cache_read`, `cache_write`, `output`, `reasoning` without double-counting. Differentiate `unknown`, `unavailable`, `stale`, `exhausted`, `not_executed`, and `uncertain`.
3. **P1 adaptive context budget:** preserve compatible native session; send only task-relevant record IDs, deltas and snippets where needed, with a recorded omission boundary. Do not resummarize the entire repository or handoff per turn; summarize after threshold and verify fidelity before replacing detailed evidence.
4. **P1 routing with constraints first:** require capability, permission, task class, health, applicable pool headroom, evidence age and user constraints; then rank eligible peers on empirically calibrated quality/cost/latency. Add hysteresis and cooldown to prevent oscillation between peers. Shared quota pools are one resource, not independent balances.
5. **P1 measured savings:** paired fixed-seed tasks for cc/cx/ag, fresh vs retained vs bounded delta; report provider-normalized input/output/cache tokens, currency where observable, latency p50/p95, correctness and uncertainty/rework rate. Unknown counters must not become zero. Bootstrap overhead and cache policy must be stated.
6. **P2 monitoring/TUI:** one snapshot read, refresh cadence independent of redraw, focus/scroll key handling and alert acknowledgement only when justified; narrow terminals and Windows non-VT fallback, no paid refresh on repaint. Show pool, model/profile, reset, provenance, freshness, effective headroom, last ask outcome and cache efficiency; avoid duplicate metric ownership.
7. **P2 loop policy:** global token/time/side-effect envelope plus per-step limits, rate-limited recursive improvement, novelty gate for reviewers, a maximum debate round budget and explicit human approval for material side effects.

## 5. Cross-axis regression / counterexamples

- Providers: cc/cx/ag × JSON success, missing usage, cumulative vs incremental usage, empty result, exit 0 with provider error, nonzero exit with reset, invalid model, auth fail, tool denied, partial output, 429.
- Pools: independent/shared × exact zero, 0.5%, 0.499%, 0.500001%, stale, UNKNOWN, changing reset, G-7D/3P-7D and multi-window exhaustion; credits are not remaining percent.
- Sessions: new/resumed/fresh, same/different stream, model/profile drift, truncated catch-up, unexpected extra vendor event, duplicate request ID, crash/restore and multi-process concurrent attempts.
- Control: permissions/read-only, child subprocess failure, idle/wall timeout, cancellation races, stdout UTF-8 replacement, output cap, schema downgrade/future-version rejection.
- UI: no TTY, VT/non-VT, redirection, 40/60/80/120/160 columns, unicode/ASCII, colors off, clock skew, refresh not causing charged work, exact consistency between JSON and human view.
- Routing: known exhausted candidate never dispatched, unknown evidence policy, shared pool capacity, scored cost tradeoff, rank determinism, cooldown, no route when all unknown/exhausted.
- Feedback: route decision ID → delivery ID → native usage → verified outcome → quota snapshot → calibrated score/alert → next decision; error path must close with typed unknown not a fictitious number.
- Release: unit/integration/property/fault/OS matrix, binary build artifacts, audit redaction, candidate-matched **full** G3 with explicit authorization only, then post-publication smoke. Do not substitute a different ag model for an untested packaged profile without disclosure.

## 6. Acceptance/closure gates (repeat until gates, not an unprovable "perfect" claim)

- G1 **traceability**: every public field and decision has one owner, source and contract/test; identify explicitly deferred features.
- G2 **safety**: zero known exhausted dispatches; no duplicate side effects after ambiguous failure; no silently lost stream or full-history re-injection.
- G3 **correctness**: errors and quota states survive provider → CLI → DB → view; all deterministic tests pass.
- G4 **efficiency**: reproducible A/B wins in quality-adjusted token/cost and latency with stated confidence; no regressed reliability.
- G5 **operability**: operator can explain the next routing choice, see stale/unknown facts, stop recursion and recover uncertainty.
- G6 **release**: Windows/Linux and Python matrix CI, no undocumented CLI behavior drift, authorized full live gate, final clean working tree and release documentation consistency.

Open issues are not closed by a document or passing unit tests alone. Outstanding: #199 and the F-02/F-03 routing-policy decisions, TUI interactive scope and true end-to-end balancing validation.

## 7. Review changes in this branch

- `peerhub/cli/monitor_view.py`: deterministic F-01 tie-to-even display repair.
- `tests/communication/diag/test_monitor_view.py`: isolated no-provider F-01 regression values.
- This report: evidence, impact, contract boundaries, closed-loop tests and stop gates.

Validation status: the 0.005 formatting defect was reproduced with CPython arithmetic; full repository pytest/Pyright and real cc/cx/ag probes **were not run in this review environment**. GitHub CI for this PR must execute before merge. No direct main mutations, no live provider calls, no release and no paid quota probes.
