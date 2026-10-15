Plan document, 2026-10-13. Reference, not an instruction file.

# Session monitoring (pre-TDD specification)

## Status, authority and scope

Design proposal for owner review; no implementation or release approval implied.
Companion to the approved [session reuse plan](plan-session-reuse-2026-10-13.md)
(SR) and [monitoring v2 plan](plan-monitoring-v2-2026-10-12.md) (MV2).
SR controls selection, handles, retention, telemetry truth and ask trailers/JSON;
MV2 controls snapshot/policy reading, collection, watch, privacy and exit precedence.
This document specifies inspection and the session export allowlist on the MV2
infrastructure. The [AS-IS analysis and canonical PR1-PR5 sequence](analysis-session-reuse-as-is-2026-10-13.md)
supersede earlier separate session PR assignments. It does not change ask
envelopes or authorize telemetry-driven switching. PR1, PR2, and PR3 are committed
(`12129517`), providing registry, receipts, durable snapshots, and fresh/exact selection.
The proposed architecture for inspection, telemetry, and monitoring has been updated
in the authoritative [Pre-TDD Renewal](peerhub-pre-tdd-renewal-2026-10-14.md) and
[Debate Ledger](peerhub-deepthink-debate-2026-10-14.md).
The 2026-10-15 [Session Usage Monitoring and Telemetry Design](peerhub-session-usage-monitoring-review-2026-10-15.md)
extends the existing `session_telemetry` receipt with versioned provider usage,
scope, provenance, and coverage fields while preserving PH-T091's single
`bridge_evidence` receipt authority; its newer contract supersedes the narrow
field list below for telemetry additions. The accompanying [Peer CLI Option
Audit](peer-cli-option-audit-2026-10-15.md) records live canary results and
candidate flags. The earlier session/monitor field proposal below remains
historical context where the newer documents do not amend it.
Proposed schema and interfaces below remain reference designs. All defaults are testable pending owner review.

The [CLI/Monitoring Addendum](peerhub-cli-monitoring-inspection-2026-10-14.md)
now owns the command boundary: `diag --live` is the read-only watcher and
`monitor` is collection plus the shared dashboard. Session panes, if enabled,
are the same read-only snapshot section in both; there is no separate
`monitor --read-only` mode. Session-specific contracts below remain proposals
only where they do not conflict with that addendum.

Code inspected: `peerhub/extensions/{diag,diag_quota,diag_watch,bridge,ask}.py`,
`peerhub/extensions/{observation,observation_model}.py`, and
`peerhub/cli/{monitor,view}.py`. Actual diagnostic entrypoint is
`ReadonlyDiag.render(sections, read_at)`; SR section 8.5 now uses that contract.
Its URI `mode=ro`, verified `query_only`, authorizer and pinned transaction are
the reader contract. Current monitor collects quota before rendering; the proposed
session reader is always read-only. The addendum changes the first-frame order
and keeps `diag --live` as the canonical read-only watch.

## 1. Terms and persisted evidence contract

| Term | Exact meaning |
|---|---|
| Session id / handle | PeerHub `sess_[0-9a-f]{16}`; the only public session id. No vendor/external runner id. |
| Session scope | Selected store plus `(stream_id, peer_id)`; generation is local to that pair. |
| Call | One distinct delivery with confirmed STARTED evidence, including later failure/cancellation. NOT_STARTED and MAY_HAVE_STARTED-only deliveries are excluded. |
| Reuse count | Lifetime number of distinct calls whose accepted mode is `resumed`, attributed to that handle. It is not successful-response count. |
| Fresh count | Lifetime calls whose accepted mode is `fresh`, attributed to that handle (normally 1). Explicit fresh is included. |
| Last call | Greatest committed session-call evidence `seq`, not pointer generation or wall-clock order. |
| Fallback | Fresh call with a non-null prior handle and reason other than `explicit_fresh`/`no_prior`. Rejection of exact selection is not fallback. |
| Active | Stored ACTIVE and eligible at frame time: resumable, vendor id and watermark present, unexpired. Compatibility with a hypothetical future binding is not asserted. |

Current tables: `bridge_sessions` is a single-slot mapping keyed by stream/peer;
`bridge_deliveries` has delivery id, seq, stream/peer, record id, attempt,
certainty, generation, result_json and terminal_at; `bridge_evidence` has seq,
delivery id, stream/peer, kind, certainty and JSON detail. There are no current
handle/mode/token columns on deliveries/evidence. canonical PR2 replaces sessions with
an MRU pointer and introduces `bridge_session_history` (H). Always read history
for identity; joining the pointer (P) only supplies `latest`. An older generation
can be latest after exact reuse. Never attribute old deliveries through P.

### Proposed writer-owned additions (required for bounded reads)

Retain existing evidence columns/triggers. Add these allowlisted evidence kinds:

| Kind | `bridge_evidence.detail` keys, required unless explicitly nullable |
|---|---|
| `session_call` | `handle`, `generation`, `mode` (`fresh`/`resumed`), `reason` (SR enum or null), `prior_handle` (handle or null), `started_at` (epoch seconds). Scope/delivery id come from table columns. |
| `session_telemetry` | `handle`, `generation`, `observed_at` (epoch), `last_input_tokens`, `context_window`, `context_pct` (each nullable), `source` (`reported`/`unknown`), `window_reported_at` (epoch or null), `model` (string or null). Occupancy is the final model request, including cache, per SR §10. |

Exactly one `session_call` receipt per delivery, committed with the first
confirmed STARTED transition. Repeated started events, terminal recovery, CLI
request-id replay and observer redraw add no receipt. The receipt identifies the
actually started session, after any proven prespawn fallback. MAY_HAVE_STARTED
without later confirmation does not fabricate a receipt. One terminal telemetry
receipt per delivery, including all-null unknown telemetry, is committed with
the durable result; it must agree with `result_json.session.telemetry` if present.
Do not change an already immutable terminal row to enrich monitoring later.

Proposed projection M, maintained atomically by Bridge writers, never readers:

```sql
CREATE TABLE bridge_session_monitor_stats (
  session_handle TEXT PRIMARY KEY REFERENCES bridge_session_history(session_handle),
  reuse_count INTEGER, fresh_count INTEGER,
  counts_complete INTEGER NOT NULL CHECK (counts_complete IN (0,1)),
  binding_json TEXT NOT NULL,
  latest_call_seq INTEGER REFERENCES bridge_evidence(seq),
  latest_telemetry_seq INTEGER REFERENCES bridge_evidence(seq),
  CHECK (reuse_count IS NULL OR reuse_count >= 0),
  CHECK (fresh_count IS NULL OR fresh_count >= 0)
);
CREATE TABLE bridge_session_monitor_scope (
  stream_id TEXT NOT NULL, peer_id TEXT NOT NULL,
  reuse_count INTEGER, fresh_count INTEGER,
  counts_complete INTEGER NOT NULL CHECK (counts_complete IN (0,1)),
  coverage_start_seq INTEGER NOT NULL,
  PRIMARY KEY (stream_id, peer_id),
  CHECK (reuse_count IS NULL OR reuse_count >= 0),
  CHECK (fresh_count IS NULL OR fresh_count >= 0)
);
CREATE UNIQUE INDEX idx_be_session_call_once ON bridge_evidence(delivery_id)
  WHERE kind = 'session_call';
CREATE UNIQUE INDEX idx_be_session_telemetry_once ON bridge_evidence(delivery_id)
  WHERE kind = 'session_telemetry';
CREATE INDEX idx_be_peer_call ON bridge_evidence(peer_id, seq DESC)
  WHERE kind = 'session_call';
CREATE INDEX idx_be_scope_call ON bridge_evidence(stream_id, peer_id, seq DESC)
  WHERE kind = 'session_call';
CREATE INDEX idx_bsh_monitor_scope ON bridge_session_history
  (stream_id, peer_id, last_seen DESC, session_generation DESC, session_handle DESC);
CREATE INDEX idx_bsh_monitor_live ON bridge_session_history
  (stream_id, peer_id, last_seen DESC, session_generation DESC, session_handle DESC)
  WHERE state != 'TOMBSTONED';
CREATE INDEX idx_bsh_monitor_active ON bridge_session_history
  (stream_id, peer_id, last_seen)
  WHERE state='ACTIVE' AND resumable=1 AND vendor_session_id IS NOT NULL
    AND context_watermark IS NOT NULL;
CREATE INDEX idx_bs_monitor_peer ON bridge_sessions
  (peer_id, updated_at DESC, stream_id);
```

`binding_json` is `{profile, model, effort, writable}`, strings nullable and
writable boolean or null. It stores effective creation binding attributes beside
H.binding (opaque hash); hashes cannot be decoded to recover permissions/model.
No workspace path, vendor id or credential enters this projection. New sessions
start with complete 0/0 counts. Receipt insertion and increment are one fenced
transaction, guarded by the unique receipt index, not observer-side counting.
The same transaction increments `bridge_session_monitor_scope` totals for the
stream/peer; those totals include tombstoned sessions, preventing false
reuse-unused alerts after an older reused session leaves the retained live set.
Scope totals use the same completeness/null rule as per-handle totals.
`coverage_start_seq` is the evidence high-water mark at monitoring migration
(0 for new scopes); all later confirmed calls must have receipts. Backfill may
reconstruct lifetime counters but never appends old calls as newly ordered
`session_call` receipts. First-STARTED gating prevents terminal recovery of an
old delivery from incrementing reconstructed totals.
Telemetry updates only M.latest_telemetry_seq, counters only on call insertion.
M.latest_call_seq advances on each call; readers invalidate older context when
the latest call lacks its own completed telemetry.

Migration/backfill and working receipt/counter writers belong together in PR2: reconstruct only from
unambiguous delivery identity and recorded mode; generation alone is not mode.
Unprovable historical totals set `counts_complete=0` and both counters null.
They stay unknown even after new calls; recent-call ratios may still be known.
No missing historical receipt is invented. Exact complete counters survive
history tombstoning; tombstoned rows use pagination, not removal by observers.

## 2. Metrics catalog and null rules

Aliases: H history, P pointer, M projection, C latest `session_call` receipt
(M.latest_call_seq), T latest telemetry receipt (M.latest_telemetry_seq),
Q latest committed account quota observation per peer/pool. Stored facts (F)
are recorded metadata, measured (M) comes from provider evidence, derived (D)
is computed. Metadata must not be described as a provider measurement.
Costs exclude indexed seek overhead: H/M/C/T are O(1) per displayed session;
Q is O(number of declared pools), at most 8 per peer. No history scans.

| Field | Exact table/column or JSON path / formula | Kind | Null / invalid rule | Read cost |
|---|---|---|---|---|
| `handle` | H.session_handle | F | Null only for legacy rows; never mint while reading | H |
| `generation` | H.session_generation | F | Missing/invalid positive integer -> null + partial | H |
| `state` | H.state | F | Unknown enum -> UNKNOWN + partial; do not rewrite expired ACTIVE | H |
| `latest` | P.session_handle = H.session_handle AND P.session_generation = H.session_generation | D | No pointer -> false; mismatched pointer -> partial | P join |
| `mode`, `reason` | C.detail `$.mode`, `$.reason` | F | No attributable call -> both null; resumed reason null; fresh SR reason required | C |
| `reuse_count` | M.reuse_count | D | Missing M / counts_complete=0 -> null, never 0 | M |
| `fresh_count` | M.fresh_count | D | Same as reuse_count | M |
| `last_used` | H.last_seen (SR last completed turn timestamp) | F | Invalid/future -> null + unknown time; provisional creation timestamp is labelled never-used when 0/0 | H |
| `created_at` | H.created_at | F | Invalid/future -> null + unknown time | H |
| `age_s` | frame epoch - H.created_at | D | Invalid/future timestamp -> null; not quota/context age | H |
| `expires_in_s` | max(0, H.last_seen + 30*86400 - frame epoch) | D | Invalid/future last_seen -> null; tombstoned -> 0 | H |
| `eligible` | H.state=ACTIVE, H.resumable=1, vendor_session_id IS NOT NULL, context_watermark IS NOT NULL, frame-H.last_seen <=2592000 | D | Unknown dependency -> null; LOST/TOMBSTONED -> false | H |
| `last_input_tokens` | T.detail `$.last_input_tokens` | M | Last request occupancy only; missing/negative/bool/conflated -> null | T |
| `context_window` | T.detail `$.context_window` | M | Positive integer, matching session/model, cached report <24h old; otherwise null | T |
| `context_pct` | round(100 * last_input_tokens / context_window, 1) | D | Either operand unknown -> null; never sum billing or use watermark | T |
| `source` | T.detail `$.source`, validated against operands | F | reported iff valid attributable numerator/window; otherwise unknown | T |
| `context_age_s`, `context_state` | frame - T.detail `$.observed_at`; rules below | D | Missing timestamp -> null/UNKNOWN | T |
| `quota` | Q.resource_pool_ref, Q.payload_json measurements/window, Q.effective_at_us, Q.state | M+D | Unobserved -> []; unknown used/reset/age remain null in each pool | Q |
| `writable` | json_extract(M.binding_json,'$.writable') | F | Missing legacy attribute -> null; never assume false or inherit current CLI flag | M |
| `binding` | H.binding as `hash`; M.binding_json `profile/model/effort`; writable above | F | Attributes individually null; hash null on missing column; never reverse hash | H+M |
| `context_watermark`, `bootstrap_truncated` | H.context_watermark, bool(H.bootstrap_truncated) | F | Missing -> null; watermark is record position, never tokens | H |
| `sessions_active`, recent ratios | Queries §3 | D | Coverage/mode incomplete -> null; no calls -> ratios null | bounded H/C |

Raw T values are never silently carried to a different handle/model. If C is
newer than T and has a different delivery_id, current tokens/window/percentage
become null/source unknown/context_state UNKNOWN; show no old percentage as
current. If occupancy is unexposed, window and percentage are also null (SR null
rule); an attributable numerator with unexposed window may remain non-null.
Percentages above 100 are preserved, not clamped. Thresholds use unrounded
ratios; formatting rounds only presentation. When stale, valid raw tokens and
percentage remain visible with STALE, but cannot enter/clear an alert.

Quota is account-level shared evidence, not session allocation or session cost.
Do not copy a stale quota block from an old ask result as today's quota: select Q
at the pinned frame time. Show all declared pools, sorted by pool id, not a
silent choice of one constraining pool. `used_pct` = 100*used_fraction using the
existing measurement normalizer (`remaining_fraction`, or valid used/limit);
`resets_in_s` = max(0, ceil(reset_epoch-frame)); no reset -> null.
`age_s` = (read_us-Q.effective_at_us)/1e6, preserving fractional seconds in JSON.
Effective time is captured_at if supplied, otherwise observed_at, with
effective_at_us checked against that rule. Q/source reports measurements;
percentage normalization, age/countdown and forecast are derived.

## 3. Exact reader interfaces, queries and staleness

Proposed interfaces in `peerhub/extensions/session_monitoring.py`; register a
new extension module in both architecture/continuity gates when implemented.
All receive an already pinned read-only connection and MV2 policy. No method
constructs Bridge, ClaimStore or an ObservationStore writer.

```python
read_session(conn, handle: str, *, now: float, policy) -> SessionSnapshot | None
read_session_page(conn, *, peer: str | None, stream: str | None,
                  after: tuple[str, str, float, int, str] | None,
                  limit: int = 20, now: float, policy) -> SessionPage
read_peer_sessions(conn, peer: str, *, streams: tuple[str, ...],
                   now: float, calls_n: int = 20, policy) -> PeerSessionAggregate
read_latest_quota(conn, peer: str, pool: str, *, now: float, policy) -> QuotaSnapshot
```

Page limit 1..100, calls_n 1..100; invalid values are usage errors (2). CLI list
adds `--limit N` and `--after TOKEN` (opaque versioned cursor encoding the sort
tuple); no default unlimited dump. List includes FRESH/ACTIVE/LOST/TOMBSTONED,
filters before pagination, sorts stream/peer ascending then last_seen,
generation and handle descending. Show requires a full handle, no prefix
resolution. Monitor uses at most 10 peers, 20 MRU streams per peer selected by
P.updated_at DESC, stream_id ASC, and 20 non-tombstoned retained rows per scope;
the pane shows the first 20 rows with `more: session list`. Overflow declares
coverage partial; aggregates below are explicitly for these selected streams,
never advertised as whole-store totals. Calls/ratios are across those streams.
Empty/invalid pointers do not select unrelated scopes; list still reads history.

SQL R, reuse_count/fresh_count (single indexed projection lookup):

```sql
SELECT CASE WHEN m.counts_complete=1 THEN m.reuse_count END AS reuse_count,
       CASE WHEN m.counts_complete=1 THEN m.fresh_count END AS fresh_count
FROM bridge_session_history h
LEFT JOIN bridge_session_monitor_stats m ON m.session_handle=h.session_handle
WHERE h.session_handle=:handle;
```

SQL A, exact per-peer active count **within selected streams**, one invocation
per stream (sum results; if any read fails the sum is null):

```sql
SELECT COUNT(*) AS sessions_active
FROM bridge_session_history
WHERE stream_id=:stream AND peer_id=:peer AND state='ACTIVE'
  AND resumable=1 AND vendor_session_id IS NOT NULL
  AND context_watermark IS NOT NULL
  AND last_seen >= :now-2592000 AND last_seen <= :now;
```

SQL A uses idx_bsh_monitor_active, not a scan of tombstones within the age range.
Monitor history reads use idx_bsh_monitor_live with its exact non-tombstone
predicate; list pages use idx_bsh_monitor_scope. Index presence/query plans are
capabilities checked before reading; absence is partial, never permission for an
unbounded fallback scan.

SQL W obtains at most N calls per selected stream via idx_be_scope_call:

```sql
SELECT seq, delivery_id, stream_id, peer_id,
       json_extract(detail,'$.handle') AS handle,
       json_extract(detail,'$.mode') AS mode,
       json_extract(detail,'$.reason') AS reason,
       json_extract(detail,'$.prior_handle') AS prior_handle
FROM bridge_evidence
WHERE stream_id=:stream AND peer_id=:peer AND kind='session_call'
ORDER BY seq DESC LIMIT :n;
```

Merge these bounded sorted lists by seq DESC, take first N across streams, then
`resumed_ratio = count(mode=resumed)/calls_observed`,
`fresh_ratio = count(mode=fresh)/calls_observed`. `calls_observed=min(N,available)`;
the window is last N confirmed calls, not last N days. E.g. fresh,resumed,resumed,
fresh,resumed gives 0.6 resumed / 0.4 fresh. With complete valid modes ratios sum
to 1.0. Malformed receipt is partial, ratios null, never skipped to make numbers
look complete. No receipts -> 0 calls and both ratios null. Legacy receipts are
not backfilled speculatively: `history_complete=false` exposes incomplete
pre-migration coverage. A recovered terminal call is counted once by delivery,
not once by each ask command; a genuinely executed reconciliation retry has its
own delivery and counts separately.

If an existing scope has older confirmed deliveries without receipts and fewer
than N post-cutover calls, both ratios are null (the known sample does not prove
the last N calls). Once N post-cutover calls cover the entire requested window,
ratios become exact and `history_complete=true` for that window, even if lifetime
totals remain unknown. For fewer calls on a new scope, the complete shorter
sample is valid. Compare W.seq to the scope's coverage_start_seq; never fill a
window by silently skipping an unclassified legacy call. On mixed scopes,
coverage is complete only if no missing older call could enter the merged last-N
window; otherwise return null ratios and history_complete=false.

Reuse-unused uses exact lifetime stream/peer totals (SQL U); fallback evaluates
W at stream/peer scope over last 5 calls. Reuse-unused means >=3 calls and zero
resumed calls ever in that stream/peer, including tombstoned handles; it does not
accuse every newly created handle of being unused. Incomplete scope counts
suppress entry. Explicit fresh calls count for this informational rule, but
never for fallback. Fallback counts only attributable default fresh calls with
prior_handle and a fallback reason.

```sql
-- SQL U: constant-size stream lifetime totals, not a history GROUP BY.
SELECT CASE WHEN counts_complete=1 THEN reuse_count END AS reuse_count,
       CASE WHEN counts_complete=1 THEN fresh_count END AS fresh_count
FROM bridge_session_monitor_scope
WHERE stream_id=:stream AND peer_id=:peer;
```

SQL Q (bounded by idx_obs_subject_kind; parse only the returned row):

```sql
SELECT capture_seq, observation_id, subject_ref, resource_pool_ref, kind, source,
       state, payload_json, observed_at, captured_at, effective_at_us
FROM observations
WHERE subject_ref=:peer AND kind=:kind AND resource_pool_ref=:pool
ORDER BY effective_at_us DESC, capture_seq DESC LIMIT 1;
```

Kinds `quota`/`rate_limit` are evaluated separately under the MV2 reader; do not
substitute one semantic for the other. Add a writer migration index
`observations(subject_ref,kind,resource_pool_ref,effective_at_us DESC,capture_seq DESC)`
to avoid scanning unrelated pools. Pool enumeration comes from bounded registered
resource_pools; unknown registration yields unknown, not unlimited capacity.

| Value | Fresh / stale / expired / unknown boundary |
|---|---|
| Context | Fresh age <300s; STALE age >=300s; UNKNOWN absent/newer unfinished call/negative age/mismatched model; window report age >=86400s invalidates window/pct. |
| Quota | Fresh age <300s; STALE age >=300s; EXPIRED frame >=reset (wins over stale); UNKNOWN missing/invalid/negative age. Stored ERROR/UNAVAILABLE is preserved separately. |
| Session eligibility | Age is inactivity since last_seen, not created_at. Exactly 30 days remains age-eligible; >30 days is expired. expires_in=0 at the boundary; no tombstone/prune write. |
| Counts/binding | Durable metadata, no 300s TTL; expose completeness. Changing frame time does not age counts into unknown. |
| Clock | One wall-clock frame time for all derivations, monotonic watch cadence. Future source times give unknown age; never clamp negative age into freshness. |

## 4. Text views and output rules

Illustrative layouts below fit 80 columns. Column order, labels and omission
rules are normative; examples use one session for brevity. Pane appears after
quota/credits and before latest asks/health. `R/F` is reuse/fresh deliveries;
`W` is writable (yes/no/?), `*` is MRU, `?` means unknown, not zero.

`peerhub monitor` and `peerhub diag --live` (same read-only pane; monitor also collects):

```text
Sessions  streams:20/peer  calls:20  account quota shared
PEER STREAM    HANDLE                 GEN STATE   R/F  CTX     W EXPIRES
cx   review    sess_8f3a9e21bc04781d*    3 ACTIVE  4/1  78.0%   no 29d
  last: resumed reason=- used:2026-10-13T00:00:00Z age:1d
  input:212160 window:272000 source:reported context-age:120s
  quota:cx:7d used:32.0% age:120s FRESH reset:1h
cx active:1 resumed:80.0% fresh:20.0% calls:5/20 scopes:1
WARN context sess_8f3a9e21bc04781d 78.0% [active]
more: peerhub session list --peer cx
```

`peerhub session list [--peer PEER] [--stream STREAM] [--limit N] [--after TOKEN]`:

```text
Sessions retained  page:1 limit:20  * latest; R/F resumed/fresh
PEER STREAM    HANDLE                 GEN STATE   R/F  CTX     W EXPIRES
cx   review    sess_8f3a9e21bc04781d*    3 ACTIVE  4/1  78.0%   no 29d
  last:resumed reason=- input:212160 window:272000 source:reported
  used:2026-10-13T00:00:00Z age:1d eligible:yes context-age:120s
  quota:cx:7d 32.0% age:120s FRESH binding:profile=cx.standard
next: none
```

`peerhub session show sess_8f3a9e21bc04781d`:

```text
Session sess_8f3a9e21bc04781d
peer:cx stream:review generation:3 state:ACTIVE latest:yes eligible:yes
last call:resumed reason:- reused:4 fresh:1 counts:complete
created:2026-10-12T00:00:00Z last-used:2026-10-13T00:00:00Z
age:1d expires-in:29d retention:30d inactivity
input:212160 window:272000 context:78.0% source:reported age:120s FRESH
quota:cx:7d used:32.0% age:120s FRESH reset:1h (account shared)
binding hash:b1 profile:cx.standard model:m effort:high writable:no
watermark:42 bootstrap-truncated:no
WARN context 78.0% [active]; informative; caller chooses next session
```

Show prints all binding attributes and all quota pools, wrapping continuation
lines; list prints binding detail only when requested session is shown. Neither
shows prompt text, response, runner id, vendor id, paths or vendor log excerpts.
Age/expiry/countdowns are compact in tables (ceil remaining seconds; whole days
floor), full epoch precision in JSON; show includes UTC RFC3339 timestamps.

`diag --line` keeps MV2's complete prefix and adds exactly one suffix:
` | sessions <peer:SUMMARY> ...`, peers lexically sorted. Each summary is
`active=A resumed=R% fresh=F% calls=K/N handle=H gen=G state=S mode=M reason=B reuse=U ctx=C source=SRC writable=W expires=E`.
Representative handle is highest context severity, then latest call seq, then
handle lexical order; no session -> handle/gen/state/mode/reason/reuse/ctx/source/
writable/expires `?`. A/F/ratios refer to §3 scope, never just this handle.
For example, the **additional suffix** is:

```text
 | sessions cx:active=1 resumed=80.0% fresh=20.0% calls=5/20
 handle=sess_8f3a9e21bc04781d gen=3 state=ACTIVE mode=resumed reason=-
 reuse=4 ctx=78.0% source=reported writable=no expires=29d
```

Those lines concatenate with spaces into one ASCII physical line. `--line` has
no terminal-width cap and always ends with one LF. It is not wrapped/truncated;
this is the intentional exception to 80-column layouts. `--line --json` is usage
error 2; the equivalent structured session summary is added to `diag --json`,
with no wholesale change to retained diagnostic sections.

### Shortening, narrow terminals and colour

Full handles are 21 characters and are printed intact at 80 columns. If a table
must shorten, display `sess_` plus at least 8 hex characters plus `~`; expand hex
prefix until unique **within that frame**, capped at the full handle. A short
label is never accepted as CLI input or described as copyable. Immediately print
`handle: <full-handle>` as a continuation for every shortened label. No OSC links,
clipboard escape sequences or hidden identifier text. Copy the full visible
handle or retrieve it from JSON/show. Prefix collision never selects a session.

At width >=80 use the table; below 80 use session cards, priority: full handle,
peer/stream, generation/state/mode/reason, R/F, context/source, writable/expiry,
then timestamps/quota/binding. Wrap on cell width (East Asian width aware), never
cut a numeric value into a different number. Stream/peer labels may right-elide
with ASCII `...` in tables and are complete in cards/show/JSON. JSON never
truncates field strings or numbers; page truncation uses explicit cursor/coverage.
At widths <21, print the full handle on its own line even if it overflows; this
is the sole card-width exception. At limited height, remove quota detail and
timestamp continuation first, then lower-priority rows with `+N more`; keep alert
summary and first session handle. Layout changes do not change the read snapshot.

Non-empty NO_COLOR suppresses all SGR colour even with FORCE_COLOR; empty
NO_COLOR is ignored. Nonzero FORCE_COLOR/CLICOLOR_FORCE may override CLICOLOR=0
only where rich interactive colour is otherwise permitted. Plain/non-TTY/JSON/
--line are escape-free. Supported VT cursor controls remain in interactive watch
under NO_COLOR; restore cursor on exit. Severity always uses INFO/WARN/CRITICAL
words, source and freshness text; colour conveys no unique information.

## 5. JSON fragments and examples (Draft 2020-12)

The following single schema bundle defines the **new session payloads**. Select
`#/$defs/monitor`, `list`, `show` or `diag` as the validation root. Monitor's
existing MV2 fields remain governed by its allowlist; insert this monitor fragment
as `sessions`, not a raw DiagnosticReport. Insert the diag fragment as the new
sessions section of retained `diag --json`/health, leaving other contracts intact.
All listed properties are required unless absent from `required`. Numeric nulls
are JSON null, never "UNKNOWN" strings. Binding hash is opaque, no path expansion.

```json
{
  "$schema":"https://json-schema.org/draft/2020-12/schema",
  "$defs":{
    "handle":{"type":["string","null"],"pattern":"^sess_[0-9a-f]{16}$"},
    "count":{"type":["integer","null"],"minimum":0},
    "seconds":{"type":["number","null"],"minimum":0},
    "text":{"type":["string","null"]},
    "freshness":{"enum":["FRESH","STALE","EXPIRED","UNKNOWN"]},
    "binding":{
      "type":"object","additionalProperties":false,
      "required":["hash","profile","model","effort"],
      "properties":{
        "hash":{"$ref":"#/$defs/text"},"profile":{"$ref":"#/$defs/text"},
        "model":{"$ref":"#/$defs/text"},"effort":{"$ref":"#/$defs/text"}
      }
    },
    "quota":{
      "type":"object","additionalProperties":false,
      "required":["pool","used_pct","resets_in_s","age_s","state","threat"],
      "properties":{
        "pool":{"type":"string"},
        "used_pct":{"type":["number","null"],"minimum":0},
        "resets_in_s":{"$ref":"#/$defs/count"},"age_s":{"$ref":"#/$defs/seconds"},
        "state":{"enum":["FRESH","STALE","EXPIRED","UNKNOWN","ERROR","UNAVAILABLE"]},
        "threat":{"enum":["clear","threat","exhausted","unknown"]}
      }
    },
    "session":{
      "type":"object","additionalProperties":false,
      "required":["handle","peer","stream","generation","state","latest","eligible",
        "mode","reason","reuse_count","fresh_count","counts_complete","created_at","last_used",
        "age_s","expires_in_s","last_input_tokens","context_window","context_pct",
        "source","context_age_s","context_state","quota","writable","binding",
        "context_watermark","bootstrap_truncated"],
      "properties":{
        "handle":{"$ref":"#/$defs/handle"},"peer":{"type":"string"},"stream":{"type":"string"},
        "generation":{"type":["integer","null"],"minimum":1},
        "state":{"enum":["FRESH","ACTIVE","LOST","TOMBSTONED","UNKNOWN"]},
        "latest":{"type":"boolean"},"eligible":{"type":["boolean","null"]},
        "mode":{"enum":["fresh","resumed",null]},
        "reason":{"enum":[null,"no_prior","explicit_fresh","binding_change",
          "workspace_generation_change","fingerprint_change","delta_too_large",
          "not_resumable","missing","tainted","retention_max_age","legacy_watermark_untrusted"]},
        "reuse_count":{"$ref":"#/$defs/count"},"fresh_count":{"$ref":"#/$defs/count"},
        "counts_complete":{"type":"boolean"},"created_at":{"$ref":"#/$defs/seconds"},
        "last_used":{"$ref":"#/$defs/seconds"},
        "age_s":{"$ref":"#/$defs/seconds"},"expires_in_s":{"$ref":"#/$defs/seconds"},
        "last_input_tokens":{"$ref":"#/$defs/count"},
        "context_window":{"type":["integer","null"],"minimum":1},
        "context_pct":{"type":["number","null"],"minimum":0},
        "source":{"enum":["reported","unknown"]},
        "context_age_s":{"$ref":"#/$defs/seconds"},"context_state":{"$ref":"#/$defs/freshness"},
        "quota":{"type":"array","maxItems":8,"items":{"$ref":"#/$defs/quota"}},
        "writable":{"type":["boolean","null"]},"binding":{"$ref":"#/$defs/binding"},
        "context_watermark":{"$ref":"#/$defs/count"},
        "bootstrap_truncated":{"type":["boolean","null"]}
      }
    },
    "aggregate":{
      "type":"object","additionalProperties":false,
      "required":["peer","streams","sessions_active","calls_n","calls_observed",
        "resumed_ratio","fresh_ratio","history_complete"],
      "properties":{
        "peer":{"type":"string"},"streams":{"type":"array","maxItems":20,"items":{"type":"string"}},
        "sessions_active":{"$ref":"#/$defs/count"},"calls_n":{"type":"integer","minimum":1,"maximum":100},
        "calls_observed":{"type":"integer","minimum":0,"maximum":100},
        "resumed_ratio":{"type":["number","null"],"minimum":0,"maximum":1},
        "fresh_ratio":{"type":["number","null"],"minimum":0,"maximum":1},
        "history_complete":{"type":"boolean"}
      }
    },
    "alert":{
      "type":"object","additionalProperties":false,
      "required":["id","kind","severity","state","acknowledged","handle","peer","stream"],
      "properties":{
        "id":{"type":"string"},
        "kind":{"enum":["CONTEXT","SESSION_EXPIRING","REUSE_UNUSED","REPEATED_FALLBACK","THREAT","EXHAUSTED"]},
        "severity":{"enum":["info","warn","critical"]},
        "state":{"enum":["active","unknown","resolved"]},
        "acknowledged":{"type":"boolean"},"handle":{"$ref":"#/$defs/handle"},
        "peer":{"type":"string"},"stream":{"$ref":"#/$defs/text"}
      }
    },
    "payload":{
      "type":"object","additionalProperties":false,
      "required":["version","status","read_at","coverage","items","peers","alerts","next_cursor","error"],
      "properties":{
        "version":{"const":"1.0"},"status":{"enum":["OK","PARTIAL","FAILED","UNAVAILABLE","SCHEMA_VERSION"]},
        "read_at":{"type":"number"},"coverage":{"enum":["complete","partial","legacy"]},
        "items":{"type":"array","maxItems":100,"items":{"$ref":"#/$defs/session"}},
        "peers":{"type":"array","maxItems":10,"items":{"$ref":"#/$defs/aggregate"}},
        "alerts":{"type":"array","items":{"$ref":"#/$defs/alert"}},
        "next_cursor":{"$ref":"#/$defs/text"},"error":{"$ref":"#/$defs/text"}
      }
    },
    "monitor":{"$ref":"#/$defs/payload"},
    "list":{"$ref":"#/$defs/payload"},
    "show":{"allOf":[{"$ref":"#/$defs/payload"},{"properties":{"items":{"maxItems":1}}}]},
    "diag":{"$ref":"#/$defs/payload"}
  }
}
```

Semantic assertions beyond structural schema: resumed => reason null; fresh =>
reason non-null; counts_complete=false => null counters; source=unknown =>
window/pct null; reported => attributable non-null operands; percentage matches
formula; eligible true => ACTIVE; failed show => empty items; successful show =>
exactly one item. No nonfinite floats or boolean-as-integer values. Lists have
empty peers (they are inspection, not aggregate watch). Diag/monitor aggregate
scope is explicit. Error strings are stable sanitized codes, not raw exceptions.

Examples below are payload instances. `E` is the exact common envelope
`{"version":"1.0","status":"OK","read_at":1791849720,"coverage":"complete","items":[],"peers":[],"alerts":[],"next_cursor":null,"error":null}`.
`S` and `A` are literal JSON objects, substituted into E for the four examples:

```json
{
  "handle":"sess_8f3a9e21bc04781d","peer":"cx","stream":"review",
  "generation":3,"state":"ACTIVE","latest":true,"eligible":true,
  "mode":"resumed","reason":null,"reuse_count":4,"fresh_count":1,
  "counts_complete":true,"created_at":1791763200,"last_used":1791849600,"age_s":86520,"expires_in_s":2591880,
  "last_input_tokens":212160,"context_window":272000,"context_pct":78.0,
  "source":"reported","context_age_s":120,"context_state":"FRESH",
  "quota":[{"pool":"cx:7d","used_pct":32.0,"resets_in_s":3600,"age_s":120,"state":"FRESH","threat":"clear"}],
  "writable":false,"binding":{"hash":"b1","profile":"cx.standard","model":"m","effort":"high"},
  "context_watermark":42,"bootstrap_truncated":false
}
```

```json
{"peer":"cx","streams":["review"],"sessions_active":1,"calls_n":20,
 "calls_observed":5,"resumed_ratio":0.8,"fresh_ratio":0.2,"history_complete":true}
```

| Interface example | Exact composition (substitute literal objects, no `$ref` on wire) |
|---|---|
| monitor --json | MV2 frame with `sessions=E`, replacing items with `[S]`, peers with `[A]`, alerts with the CONTEXT warn object below; one NDJSON frame per cycle |
| session list --json | E with items `[S]`; peers `[]`; same entry-only alert; next_cursor null for final page |
| session show HANDLE --json | E with items `[S]`; peers `[]`; same alert; invalid syntax/not-found => items `[]`, status FAILED, error `invalid_session_handle_syntax`/`session_not_found`, exit 2 |
| diag --json (structured --line equivalent) | New sessions section E with items `[S]`, peers `[A]`, same alert; existing diagnostic JSON fields retained |

```json
{"id":"context:sess_8f3a9e21bc04781d","kind":"CONTEXT","severity":"warn",
 "state":"active","acknowledged":false,"handle":"sess_8f3a9e21bc04781d",
 "peer":"cx","stream":"review"}
```

Unknown example: replace S tokens/window/pct/context_age with null, source with
unknown, context_state UNKNOWN, quota with [], writable with null; alerts []
unless a watch already latched an alert, which becomes unknown. Before registry
migration, coverage legacy, status PARTIAL, handle null, counters null and
counts_complete=false. No synthetic public id. Error codes include
`session_schema_missing`, `session_projection_missing`, `session_receipt_invalid`,
`store_unreadable`, `schema_version_error`; paths and exception details excluded.

## 6. Alerts in the MV2 state machine

Pure evaluator, same shared policy for monitor, `diag --live`, list/show and
diag. Watch keeps in-memory state only; one-shot commands evaluate entry only.
Key context/expiry by handle, reuse-unused/fallback by stream+peer, quota by
peer+pool (one shared alert, never duplicated for every session). States
active -> resolved on valid clear; active -> unknown on invalid/stale evidence;
unknown -> active/resolved only on qualifying distinct evidence. Unknown is
latched, not cleared. Session removal/tombstone needs explicit committed lifecycle
evidence to resolve context/expiry; disappearing from a page is not resolution.

| Kind / severity | Enter (inclusive unless stated) | Clear / hysteresis |
|---|---|---|
| CONTEXT warn / critical | Fresh reported context >=70% warn; >=90% critical. Same id changes severity. | Critical downgrades after 2 distinct fresh telemetry receipts <85%; warn resolves after 2 receipts <65%. 65..69.999 holds warn, 85..89.999 holds critical. >=90 escalates immediately and resets clear streak. |
| SESSION_EXPIRING warn | ACTIVE eligible session with 0<=expires_in_s<259200 (strictly <3 days) | 2 distinct completion observations giving expires_in>=345600 (4 days); expiry beyond 30 days stays active alert, now labelled expired, until committed tombstone/loss resolves. |
| REUSE_UNUSED info | Lifetime stream/peer reuse_count=0, fresh_count>=3, complete scope counts (SQL U) | First distinct resumed call in stream resolves; dropping scope or missing counts never clears. |
| REPEATED_FALLBACK warn | >=2 fallback calls in last 5 calls, even if only 2 total available | 2 distinct new call receipts whose last-5 window contains 0 fallback; a redraw or shrinking unknown window cannot clear. |
| THREAT warn | MV2 fresh gated forecast projected reset usage >=105% | MV2 2 distinct valid quota observations projected <100%; gates/unknown/stale suppress clear. |
| EXHAUSTED critical | Fresh measured quota >=100% used; supersedes THREAT for that pool | Fresh reset or measured recovery per MV2; no stale reset countdown clears it. |

Use unrounded context values, not printed percentages (69.96 displays 70.0 but
does not enter). For context clear, 84.99 qualifies, 85 does not; a receipt at
>=85 breaks the critical clear streak. On downgrade, recompute warn state from
the two receipts: both <65 resolves, otherwise warn remains. Counter resets and
unverified occupancy produce unknown, never low synthetic utilization.

Distinctness tokens: telemetry evidence seq for context, completed delivery id
for renewal, session_call seq for stream windows, observation_id/capture_seq for
quota. At a stale-to-fresh transition without new identity, do not advance clear
streaks. 1 Hz redraw updates age/expiry only. Session expiry can enter once on a
timer crossing <3 days without a new call; it cannot clear on that timer.
Transitions are announced once per id/state/severity. Stable first-alert order:
critical before warn before info, then kind, peer, stream, handle/pool lexical.

Ack: no new durable ack command. Proposed watch input `a` acknowledges all
currently visible active alerts until state/severity changes; `q` exits 0.
Ack stops repeat emphasis/announcement, adds `[ack]`, and is local to this watch.
It neither resolves the alert, changes diagnostic status/exit, nor persists
across process restart. Escalation/new activation resets ack; unknown transition
does not silently discard it. Non-TTY/NDJSON never reads stdin; one-shot ack=false.

| Condition | Exit mapping |
|---|---|
| Argument/invalid handle/not-found | 2, evaluated before reader work |
| Missing selected DB | 5; no probes/store creation |
| Unsupported core schema version | 6; do not guess a session schema |
| Unreadable/corrupt/partial evidence, missing required session columns/projection | 4; alerts still reported independently |
| Diagnostic status OK, active/latched unknown warn or critical | 1 ALERT, including context/expiry/fallback; ack does not mask it |
| Diagnostic status OK, only info or no alerts | 0; reuse-unused alone never ALERT |
| Ctrl-C / q in watch | 0, restore terminal; finite cycles use final-frame precedence |

Precedence after valid arguments: 5 > 6 > 4 > 1 > 0, as MV2 (not numerical order).
PROBE_FAULT retains exit 4. Unknown unobserved telemetry alone is honest OK,
not partial and not ALERT. Session list/show adopt diagnostic inspection mapping;
LOST/tombstoned **show** succeeds (0 without qualifying alerts), unlike SR's ask
selection error. Ask's existing exit/trailer/advisory mapping is unchanged.
All alerts inform only: no admission guard, pruning, provider call, automatic
fresh session/model change, write permission change, steering or prompt injection.

## 7. Read-only guarantees, budget, privacy and failures

| Contract | Acceptance criterion |
|---|---|
| Store ownership | Global --db/env/discovery preserved. Missing explicit store remains missing; no fallback/bootstrap. Use mode=ro, query_only verified and authorizer denies DML/DDL/ATTACH/writable PRAGMAs. No Bridge initialization/migration. |
| Snapshot | Sessions/pointers/counts/evidence/quota in one pinned transaction with one read_at. Concurrent writers are visible next frame, never halfway through counters/receipts. |
| Zero provider work | Session read path, list/show/diag/diag --live spawn zero subprocesses, network calls, vendor DB reads, rollout parsing or cache refreshes. `monitor` collection is explicit and separate from its snapshot reader. |
| Complexity | O(displayed sessions + 20 streams/peer * N + declared pools), ignoring B-tree seeks; bounds above are enforced. Lifetime counts are O(1) projection reads. No SELECT * on result_json or linear delivery/observation history scans. |
| Refresh budget | For 3 peers, 20 streams each, 20 retained rows/scope, N=20, 8 pools/peer, <=150ms reader p95 on warmed local SQLite; <=1MiB new session payload/frame. 1Hz redraw performs zero SQL. Benchmark with >=100k unrelated deliveries/evidence/observations; p95 is a benchmark gate, deterministic tests assert query plans/row bounds. |
| Lock/time budget | Reader busy timeout <=250ms, query cancellation at 500ms; total session refresh <=750ms. Timeout yields partial last snapshot marked stale/unknown with stable error, never a write retry/repair. |
| Privacy | Allowlist reconstruction only. No vendor/external/execution/account/credit ids, paths, raw provider output, prompts or responses. Public handles, peer/stream ids and model/profile/effort names are allowed; warn in docs that user-supplied stream names may themselves contain sensitive text. Never fetch record body to compute metrics. |
| Missing tables/columns | Supported core store before SR migration: render legacy section with explicit missing capabilities and nulls; monitor/diag/list/show inspection is partial exit 4. Do not reinterpret old sessions as pointers, create handles or run migration. SR-ready store with no sessions is OK empty. |
| Unknown/malformed data | Unexposed telemetry is OK null. Invalid JSON, negative/nonfinite numbers, projection inconsistency or bad handles are partial in that section; other quota/health sections remain readable. Show never redirects. Invalid values suppressed, sanitized stable errors, zero raw payload disclosure. |
| Pointer drift | A dangling/mismatched P yields partial/latest false for affected scope. History/show still readable; no repair, tombstone or counter recomputation. |
| Projection loss | Missing M gives null totals/binding attributes; expose partial. Do not scan all deliveries as a fallback. Writer-side explicit rebuild is outside inspection. |
| Growth | Retention does not bound tombstone lifetime: paginate/index history. Over 20 scopes or 10 peers means explicit coverage partial, not misleading global totals. Additional quota pools yield partial rather than silently exporting >8. |

Keep command errors separate from unknown provider measurements. Log inspection
from retained diagnostics is not part of this new session export. Never dump a
DiagnosticReport wholesale into monitor JSON; retained diag fields keep their
existing contract, while its new session section follows this stricter allowlist.

## 8. Numbered TEST CATALOG

All tests deterministic; no live quota/provider requirement. U=unit,
I=SQLite integration, C=CLI emitted-byte contract, B=benchmark. Modules below
are planned test files: prefix D=`tests/communication/diag/`,
E=`tests/communication/e2e/`, S=`tests/communication/bridge/`.
Each row is a failing-test contract, not a claim of implementation coverage.

| ID | Level | Given | When | Then | Module |
|---|---|---|---|---|---|
| TC-MON-S01 | I | SR history gen1/gen3; pointer returns to gen1 | Read pane/show | Original handles/generations, gen1 latest, no attribution to gen3 | D/test_session_monitoring.py |
| TC-MON-S02 | I | Fresh delivery then 4 resumed, including failed/cancelled | Read counts | fresh=1 reuse=4; confirmed calls counted regardless of response success | S/test_session_monitor_projection.py |
| TC-MON-S03 | I | Same started delivery event repeated, request-id replay and terminal recovery | Update projection/read | Exactly one call receipt/increment; no observation-side writes | S/test_session_monitor_projection.py |
| TC-MON-S04 | I | NOT_STARTED, MAY_HAVE_STARTED-only and prespawn fallback | Inspect | First two excluded; fallback receipt names actual started handle only | S/test_session_monitor_projection.py |
| TC-MON-S05 | I | Unprovable legacy mode/counts | Backfill then add known call | Complete=false, lifetime totals null; recent window explicitly incomplete | S/test_session_monitor_projection.py |
| TC-MON-S06 | I | Fresh,resumed,resumed,fresh,resumed plus old calls | Read last N=5 | calls=5, ratios 0.6/0.4; older calls excluded | D/test_session_monitoring.py |
| TC-MON-S07 | I | Calls distributed across 2 selected streams, other peer/store | Merge last N | seq-order exact peer/scope window; no cross-store/peer leakage | D/test_session_monitoring.py |
| TC-MON-S08 | U | Zero calls; malformed mode receipt; N 0/101 | Aggregate/validate | Empty ratios null; malformed partial; invalid N exit2 | D/test_session_monitoring.py |
| TC-MON-S09 | I | ACTIVE eligible, LOST, FRESH, tombstone, missing vendor/watermark | Count active | Only eligible ACTIVE in selected streams counted | D/test_session_monitoring.py |
| TC-MON-S10 | U | last_seen exactly 30d and >30d; age since creation differs | Derive expiry | Boundary eligible, >30d false; expires=0; no state write | D/test_session_monitoring.py |
| TC-MON-S11 | U | Reported final occupancy 212160/window272000; billing much higher | Derive context | 78.0% reported; cache included; no billing/watermark substitution | D/test_session_monitoring.py |
| TC-MON-S12 | U | Tokens known/window missing; tool-loop breakdown missing | Render metrics | First keeps tokens; latter all three null; source unknown | D/test_session_monitoring.py |
| TC-MON-S13 | U | Matching window age 86399/86400; wrong handle/model; negative/bool | Validate | Only attributable valid <24h window accepted; invalid null/partial as §7 | D/test_session_monitoring.py |
| TC-MON-S14 | I | Old measured telemetry then newer unfinished resumed call | Read | Old context suppressed as current; source unknown, counts advance | D/test_session_monitoring.py |
| TC-MON-S15 | U | Context/quota age 299.999/300; reset equals frame; future time | Evaluate | Fresh/stale exact; quota expired at reset; future age null/unknown | D/test_session_monitoring.py |
| TC-MON-S16 | I | Multiple account pools and newer effective time tie | Read quota | All declared pools sorted; tie capture_seq; account sharing explicit | D/test_session_monitoring.py |
| TC-MON-S17 | U | Missing quota measurements/reset and unexposed tokens | Format | Null stays unknown, never zero/unlimited; no live probe | D/test_session_monitoring.py |
| TC-MON-S18 | I | Same binding hash, missing writable attrs; read-only current flags | Inspect | Stored attrs only; legacy writable null, no permission inheritance | D/test_session_monitoring.py |
| TC-MON-S19 | C | 80-column fixture for all 4 views | Emit bytes | Layout/order/labels as §4; full handles visible; --line exactly one LF | E/test_cli_session_monitoring.py |
| TC-MON-S20 | C | Widths 79/40/20; collision in first 8 hex; CJK stream | Render | Cards/cell widths; prefix expanded, full handle continuation; no CLI prefix lookup | E/test_cli_session_monitoring.py |
| TC-MON-S21 | C | Small height and >20 sessions | Render pane | Context/alert and first handle retained; +N more, coverage/page explicit | E/test_cli_session_monitoring.py |
| TC-MON-S22 | C | NO_COLOR empty/nonempty, FORCE_COLOR, non-TTY and plain | Render matrix | Empty ignored, nonempty wins, forced colour only rich TTY; all other output escape-free | E/test_cli_session_monitoring.py |
| TC-MON-S23 | C | VT failure, resize, Ctrl-C/q | Watch | Append fallback, new width applies; cursor restored; exit0 | E/test_cli_session_monitoring.py |
| TC-MON-S24 | C | MV2 --line prefix and multiple peers/sessions | Emit suffix | Existing prefix retained; deterministic representative; full handle, no width cap | E/test_cli_session_monitoring.py |
| TC-MON-S25 | C | --line --json; invalid/unknown show handle | Invoke | Usage2; empty show payload, no redirection or fabricated session | E/test_cli_session_monitoring.py |
| TC-MON-S26 | U | E/S/A plus unknown/legacy/error examples | Validate each root | Draft202012 schema and semantic assertions pass; extra/vendor field rejected | D/test_session_monitor_schema.py |
| TC-MON-S27 | C | Monitor cycles and list/show/diag JSON | Emit | NDJSON one frame/cycle; correct fragment, no truncation; retained diag contracts intact | E/test_cli_session_monitoring.py |
| TC-MON-S28 | U | Fresh context 69.96/70/89.99/90/101 | Evaluate entries | None/warn/warn/critical/critical; unrounded comparison, no 100 clamp | D/test_session_monitor_alerts.py |
| TC-MON-S29 | U | Critical then repeated same receipt and two distinct <85; warn <65 | Evaluate clears | Duplicate no progress; two clear/downgrade; 85/65 break streaks | D/test_session_monitor_alerts.py |
| TC-MON-S30 | U | Active context alert then stale/unknown/counter-reset evidence | Redraw/evaluate | Unknown latch, never resolved; fresh distinct recovery required | D/test_session_monitor_alerts.py |
| TC-MON-S31 | U | expires exactly 3d then below; renewal >=4d twice | Tick/renew | Entry only below3d; timer no clear; 2 distinct completions clear | D/test_session_monitor_alerts.py |
| TC-MON-S32 | U | 3 fresh stream calls, reuse0; incomplete counts; reused tombstone; first resumed | Evaluate | Info only with complete zero lifetime reuse; resumed resolves; tombstone reuse prevents false alert | D/test_session_monitor_alerts.py |
| TC-MON-S33 | U | Last5 includes 2 fallbacks vs explicit fresh/no_prior | Evaluate and slide window | Only true fallbacks warn; 2 new zero-fallback windows clear | D/test_session_monitor_alerts.py |
| TC-MON-S34 | U | Quota forecast105/100 and gated/ungated; exhausted100 | Evaluate | MV2 threat gate/2 <100 clear; exhausted supersedes; stale cannot clear | D/test_session_monitor_alerts.py |
| TC-MON-S35 | U | Same account pool on 3 session rows | Evaluate alerts | One quota alert; stable severity/kind/scope ordering | D/test_session_monitor_alerts.py |
| TC-MON-S36 | C | Active alert, a key, escalation, restart/non-TTY | Ack/watch | Local ack no exit suppression; escalation/reset removes ack; no non-TTY stdin | E/test_cli_session_monitoring.py |
| TC-MON-S37 | C | OK info/warn/critical and partial+alert/probe fault/schema/missing DB | Return exit | 0/1/1; failure precedence4/6/5; ask mapping unchanged | E/test_cli_session_monitoring.py |
| TC-MON-S38 | I | Database/log hashes and authorizer trace before all readers | Inspect/expiry/ack | Zero DML/DDL/repair/prune/migration; store contents/log unchanged | D/test_session_monitor_readonly.py |
| TC-MON-S39 | C | Missing explicit store, absent provider binaries/network traps | Run all read commands | Missing5 without creation; existing read paths zero provider/vendor-file calls | E/test_cli_session_monitoring.py |
| TC-MON-S40 | I | Writer changes pointer/stats/quota between section reads | Pin snapshot | One consistent old snapshot then consistent new frame | D/test_session_monitor_readonly.py |
| TC-MON-S41 | I | Pre-SR table, absent projection, future core schema | Inspect | Legacy partial4/nulls, missing projection4; future6; no migration | D/test_session_monitoring.py |
| TC-MON-S42 | I | Dangling pointer, malformed receipt/JSON, unknown enums, corrupt row | Inspect | Affected section partial, safe sanitized errors; other sections usable | D/test_session_monitoring.py |
| TC-MON-S43 | C | Sentinel prompt/response/vendor/account/path fields in results/evidence | Export all views | No sentinel leakage; explicit allowlist excludes raw result/detail/body | E/test_cli_session_monitoring.py |
| TC-MON-S44 | I | 100k unrelated history rows, >page limit, tombstones, filters | Page/read windows | Indexed bounded queries, stable cursor/no duplicates; scope coverage explicit | D/test_session_monitor_budget.py |
| TC-MON-S45 | B | Warm fixture within §7 bounds plus 100k unrelated rows | Measure refresh | p95<=150ms, payload<=1MiB; redraw zero SQL; report machine/fixture | D/test_session_monitor_budget.py |
| TC-MON-S46 | I | Busy reader/long query deadline | Refresh | <=750ms, partial sanitized timeout; no repair/retry writes | D/test_session_monitor_budget.py |
| TC-MON-S47 | C | Truncated bootstrap and retention-expired/tombstoned handle | List/show | Explicit fields/ineligibility; no ask-style refusal for inspection, no tombstone mutation | E/test_cli_session_monitoring.py |
| TC-MON-S48 | I | Projection receipt insertion crashes/retries; counters lost | Resume writer/read | Atomic receipt/counters once; reader null+partial, never history recount | S/test_session_monitor_projection.py |

Catalog count: **48** (TC-MON-S01 through TC-MON-S48).

## 9. Reconciled PR placement and dependencies

Use the single [canonical PR1-PR5 sequence](analysis-session-reuse-as-is-2026-10-13.md#one-reconciled-pr-sequence)
and its [bounded live checks](analysis-session-reuse-as-is-2026-10-13.md#live-check-plan-explicit-opt-in-not-executed-here).
There is no independent session PR order or duplicate list/show implementation.
PR4 requires the final shared MV2 snapshot/policy reader baseline; PR5 also
requires shared colour/watch/export infrastructure. The separate MV2
quota/infrastructure plan retains its own prerequisites; its session portions are integrated by canonical PR5 after the shared infrastructure
exists. Quota-only MV2 work can proceed independently.

| Monitoring contract | Canonical session placement | Dependency / independently green boundary |
|---|---|---|
| Public resume-preference removal, static capability, safe prompt watermark, all docs/help/CHANGELOG | PR1 | Complete deletion across CLI/API/kwargs/metadata writers/helpers now; no later PR cleanup |
| Retained identity, metadata projections/indexes, safe migration/backfill | PR2 | Convert all runtime old-table consumers together, including default compatible-history resolution |
| Exactly-once confirmed-call receipts/counters and terminal unknown telemetry | PR2 | Schema and real writers land together; no unused projection or speculative legacy counts |
| Fresh/exact selection and selector-attributed final outputs/receipts | PR3 | CLI/API/Bridge/replay/queued failure handling ship together; no caller resume preference |
| Attributable final-request context/window provenance | PR4 | Use PR2 receipt schema; unknowns stay null and past terminal rows are immutable |
| Shared bounded session snapshots, list/show, diag sections/schema | PR4 | Requires final MV2 shared snapshot/policy reader baseline; extend TC-SR-77 on the same readers and existing ReadonlyDiag.render |
| Session alert policy/exit behavior, diag line, pane/watch ack, NDJSON and documentation | PR5 | PR4 snapshots plus MV2 snapshot/policy/colour/shared-watch/export infrastructure |

PR2 already emits null telemetry receipts on terminal calls; PR4 measures only
attributable future values rather than rewriting old results. No standalone
session release number is introduced. SR targets 0.18.0 as a plan, not a version
bump in every feature PR; release requires the normal candidate-matched gates.
SR execution/inspection can work before optional PR5 monitor UI. MV2 on an
unmigrated store reports unavailable/partial, never migrates during reads.

Writer projection tests precede reader/UI tests. Every implementation PR runs
`python -m pytest -q`, `pyright`, command inventory and applicable extension
gates, updates its public docs/help/changelog immediately, and regenerates the
seal after any `docs/m1_spec` edit. No unimplemented API, alias, second collector
or disposable watcher is used to make an intermediate PR appear green. Shared
policy belongs to extensions and presentation to CLI; Core owns no telemetry.
Paid tests require explicit opt-in and are not run by this documentation task.

## 10. Open questions for the owner

| Question | Proposed default used by this spec/tests | Decision impact |
|---|---|---|
| Q1: Exact lifetime counts need writer-owned projection/receipts. Accept this schema addition? | Yes; unknown historical counts rather than speculative reconstruction | PR2 schema and writers together; blocking for exact bounded metrics |
| Q2: Count confirmed executed deliveries or accepted selections/successful responses? | Confirmed STARTED, including later failures; exclude unconfirmed uncertainty | Metric semantics and receipt writer gate; approve before TDD |
| Q3: Should session warn/critical affect diagnostic ALERT exit1? | Yes when diagnostic status OK; reuse-unused info stays0; ack never suppresses exit | Explicit extension of MV2 exit1 beyond quota alerts; ask exits unchanged |
| Q4: Accept local `a` ack and `q` watch controls? | In-memory visible-alert ack, no persistent ack or stdin in NDJSON | Canonical PR5 on MV2 shared-watch infrastructure |
| Q5: Whole-store active totals or bounded selected-stream totals? | 20 MRU streams/peer, labelled scope/coverage; paged history | Whole-store exact totals would need additional expiry-aware projections; cannot promise bounded reads by scanning all history |
| Q6: Approve session/context policies and performance target? | 300s context TTL, 70/90 enter, 65/85 two-receipt clears, <3d expiry/4d clear; warmed150ms p95 | Policy constants/benchmark hardware agreement; quota thresholds remain MV2 |

No new provider-verification decision here: cc/ag context windows stay unknown
until SR's empirical verification work establishes attributable runtime reports.
