# Design: Issue Resolution, Execution Safety & Monitor UX (2026-10-18)

> **Status:** Ratified by Available-Peer Consensus (`ag.effort` + `cx.effort`). Slice S1 authorized for implementation.
> **Scope:** Authoritative technical contracts resolving open issues (#117, #118, #120, #121, #123, #125, #126, #127, #130, #134, #135, #136, #140, #145, #146, #147) strictly aligned with actual source schemas, SQLite transaction invariants, and CONVENTION.md rules.

---

## 1. Concrete Contracts & Transaction Placement

### Slice S1: Execution Safety, Boundaries & Stream Fencing

#### Issue #140: Native `ag` Fail-Closed Execution Policy
- **Immutable Identity & Replay Contract:**
  - In `peerhub/extensions/ask.py`, immutable prompt, target, binding, and selector identity validation occurs **before** replay lookup. Identity mismatches raise `IdempotencyConflictError` with process spawn forbidden.
  - Only matching, acknowledged, durable outcomes replay.
  - Unacknowledged pending previous requests are denied read-only execution.
- **Native Policy Hook (`validate_execution_policy`):**
  - Implemented on native runtime targets (`CliRuntimeTarget`) in `peerhub/extensions/adapters/base.py`.
  - For a native runtime target whose canonical `runtime_kind` is `"ag"`: reject execution without writable consent using a typed prespawn policy error (`PrespawnError`). Explain that `--writable` permits unconfined write capability. Do not check or require a nonexistent `.kind` property.
  - Only native runtime execution is restricted; constructors, discovery, fingerprints, and custom/fake runtimes are unaffected (no string name-matching on fake runtimes).
- **Execution Lifecycle Placement:**
  - In `peerhub/extensions/ask.py`, `target.validate_execution_policy()` is invoked **after** matching acknowledged cached replay returns and **before** scratch directory creation, `CoreStore`, claim acquisition, or record appending.
  - In `peerhub/extensions/adapters/base.py`, native `deliver()` calls `validate_execution_policy()` again pre-spawn as defense-in-depth.
- **Refusal Model & Live Support Cleanup:**
  - Remove inference in `_ag_read_only_refusal` that `writable=False` implies zero tool effects.
  - Live test harness passes `--writable` for `ag` in an isolated scratch workspace (noting isolation is not engine-level confinement), preserving catalog requirement IDs, terminal/nonempty assertions, usage, session, and zero project-mutation checks. Documented breaking change for v0.20.0.

#### Issue #125: Single-Peer Ask Stream Fencing
- **Transaction Guard Placement (`peerhub/extensions/ask.py`):**
  - Inside `store.append_record(..., guard=prompt_guard)` under existing `BEGIN IMMEDIATE`:
    ```python
    def prompt_guard(conn: sqlite3.Connection, **scope: Any) -> None:
        if scope["stream_id"] != token.stream_id:
            raise ClaimScopeError("prompt and delivery claim must share a Stream")
        claims.check(conn, token)
        # Early-conflict cursor scan over actual ask records (no whole-history materialization)
        cursor = conn.execute(
            "SELECT targets_json, metadata_json, idempotency_key FROM records WHERE stream_id=? AND kind='prompt'",
            (token.stream_id,)
        )
        for t_json, m_json, ikey in cursor:
            # Recognize established ask prompts by idempotency key prefix AND established metadata
            # Arbitrary Core metadata runtime_kind or binding alone without ask: prefix does not bind
            if ikey and ikey.startswith("ask:") and m_json:
                meta = json.loads(m_json)
                if "runtime_kind" in meta or "binding" in meta:
                    targets = json.loads(t_json) if t_json else []
                    if targets and peer_id not in targets:
                        raise StreamPeerMismatchError(
                            f"Stream '{token.stream_id}' is bound to peer '{targets[0]}'; cannot dispatch to '{peer_id}'. "
                            "Use dedicated role streams per peer."
                        )
    ```
  - Replay takes precedence; historical mixed ask streams reject *new* work.
  - Tests verify: unrelated leading Core prompt allowed; arbitrary Core prompt with metadata does not bind; later foreign ask target rejected; historical mixed streams reject new turns; concurrent loser leaves no prompt, delivery, or offset mutation. Core remains completely unmodified.

#### Issue #126: Factual Evidence for Tool-Only Turns
- **Specification:**
  - Do NOT synthesize invented text like `"Completed via tool execution"`.
  - Retain proven terminal status (`certainty: TERMINAL`).
  - Add explicit envelope indicators: `empty_response: true` and `task_effects: "unverified"` (both survive durable replay). Empty text alone never forces failure.
  - Incomplete terminal evidence remains classified as `uncertain`.
- **Module Ownership:** `peerhub/extensions/adapters/base.py`, `peerhub/extensions/ask.py`.

#### Issue #127: Terminal Error Capture & Narration Segregation
- **Specification:**
  - Retain bounded, sanitized structured terminal provider error/reset metadata in `detail.error` after verbose initialization prefixes.
  - Oversized events may preserve terminal error metadata but never prove completion or lack of side effects; negative fixtures test hidden tools, conflicting identities, missing completion, and out-of-order events. Preserve `status: uncertain`.
  - Segregate narration from `response` only when provider events cleanly delimit a distinct final phase.
- **Module Ownership:** `peerhub/extensions/adapters/base.py`, `peerhub/extensions/adapters/process.py`.

---

### Slice S2: Guidance Registry, Lessons Slices & Bare CLI

#### Issue #117: S2-A Guidance Registry & Lessons Catalog Slices
- **Registry & Concrete Error Inventory:**
  - Inventory emitted statuses (`delivered`, `error`, `uncertain`, `pending_earlier_record`, `blocked_uncertain`, `not_executed`, `session_unavailable`, `failed_not_started`) and typed exceptions (`ClaimHeldError`, `StreamPeerMismatchError`, `SessionBridgeError`, `StorageReadOnlyError`, `StorageCorruptError`, `PrespawnError`). Test emitted outcomes independently against `peerhub/cli/recovery.py`.
  - Resolve exact absolute `--db` path and stream; construct shell-safe argv (with Windows-safe quoting for human output). Offline `peerhub explain CODE` remains generic.
  - Eliminate false exactly-once claims; instruct operators to inspect external effects before starting a new stream. Disclose that `diag health` tests store connectivity only, not active claims.
- **Lessons TOML & Keyword Search (Comment Slices 2 & 3):**
  - Packaged data: `peerhub/config_data/lessons.toml` schema: `id`, `rule`, `why`, `how`, `scope_version`, `source_url`, `checked`, `status`.
  - Search engine: In `peerhub explain`, perform case-insensitive exact code/lesson ID match first, followed by deterministic AND-token keyword search over rules/summaries. Max 10 hits + omitted count; retired IDs return successor notice; collisions rejected.
  - Generated `docs/lessons.md` with build-time parity test, GitHub lesson issue template and label, guide pointer, wheel/sdist packaging inclusion.
  - Seeds: install/source mismatch, ag permissions fail-closed policy, ag silence; uncertain/claim guidance in registry. Freshness hint: `gh issue list -R greatgc-flow/peerhub --label lesson --state all`.

#### Issue #120: Peer Launch Failure Decoding
- **Specification:**
  - Capture subprocess stderr preserving UTF-8 structured stdout. For Windows stderr fallback, use system-defined encoding (`locale.getpreferredencoding(False)`) with representative byte fixtures; never unconditionally decode OEM or assume CP949. Record encoding evidence limitations honestly without claiming exact historical reproduction.
  - Map missing executable to typed guidance code `ask_launch_failed`.
- **Module Ownership:** `peerhub/extensions/adapters/process.py`, `peerhub/cli/recovery.py`.

#### Issue #121: Bare CLI & Missing-Store Syntax
- **Specification:**
  - Bare `peerhub` without subcommands runs `peerhub guide` (exit 0).
  - Missing store exits 5 and prints stderr containing selected path, source, `Learn: peerhub guide`, and valid quoted creation command placing global `--db` before subcommand:
    `peerhub --db "C:\path\to\core.db" peer register --peer local --name local`
  - Zero auto-created databases.
- **Module Ownership:** `peerhub/cli/app.py`, `peerhub/cli/store_select.py`.

---

### Slice S3: Top-Level Readiness Command & 22nd-Century Monitor UX

#### Issue #145: Top-Level `peerhub readiness` Command
- **Diagnostic Invariant Preservation (CONVENTION.md Line 17):**
  - Diagnostics (`peerhub diag`) must NEVER write, collect observations, or contact providers.
  - Dedicated **top-level `peerhub readiness` command** (no `diag` alias). App routes directly before generic `CoreStore` initialization.
- **Cached Mode (Default):**
  - Read-only transaction against global `--db`. No subprocesses, no discovery/auth checks, no migration. Missing store exits 5 with zero writes.
  - Cached readiness establishes recorded observations, NOT present live availability; capability remains unknown/unsupported absent fresh evidence.
  - Report recorded connectivity evidence, quota freshness and execution capability separately for each profile; cached success does not mean present availability. Zero artifact file written unless `--artifact` is explicitly passed.
- **Active Probe Mode (`--probe minimal`):**
  - Only direct internally supplied scratch DB/workspace outside repo bypasses ambient `PEERHUB_DB`. Never creates/modifies source DB.
  - Sequential single attempt per profile; strictly bounded call limits and positive finite deadlines; estimated tokens/character budget without exact token cap promise.
  - Discloses fresh session and binding changes; no automatic cross-run reuse.
  - Known exhausted pools skipped unless explicit override. Active `ag` probe skipped without `--writable`.
  - Uncertain/unconfirmed termination stops the probe loop immediately and marks remaining profiles unattempted. Retains bounded sanitized report and store.
  - Cached missing-source inspection exits 5 without writes. Explicit probes may proceed after full argument/consent validation, recording inspection-source evidence as unavailable and writing only to scratch.
  - Actual model is unknown absent provider-returned evidence. Target roughly 200 tokens through a documented character budget or estimate, without an exact token-count guarantee or tokenizer dependency.

#### Issues #146 & #147: Sleek 22nd-Century Monitor UX
- **Combined Specification (Agreed by Root & CX):**
  - **Visual Hierarchy:** Restrained cyan/teal accent, neutral secondary, high-visibility alerts. All quota pools aligned at the very top immediately beneath the status line.
  - **Dynamic Pool Discovery:** Discover all actual quota pools dynamically from read model (no hardcoded pool counts).
  - **Intuitive Bar Semantics:** Bar fill direction matches remaining percentage (empty = 0%, full = 100%).
  - **Disentangled Metrics:** Separate `Reset (Local Time)` from `Burn Forecast (ETA)` into distinct columns.
  - **Terminal Dimensions & Fallback:** Fits reference 8-pool fixture in standard 100x30 terminal above fold (used as layout reference, not arbitrary-pool guarantee). On narrow/short terminals, renders compact quota table with clear clipping bounds and hidden pool count disclosure.
  - **Compatibility:** Zero extra provider probes; Unicode with clean plain ASCII fallback; no new TUI libraries, animations, or dependencies; `--json` schema completely unchanged. Preserve full live catalog, continuity, and workspace isolation.
- **Module Ownership:** `peerhub/cli/monitor.py`, `peerhub/cli/monitor_view.py`, `peerhub/extensions/monitor_read_model.py`.

---

### Slice S4: Documentation & Field Guidance

- **#118 (`ag` silence timeout):** Document silence timeout default is `None` (disabled); leave unset or set `>= overall timeout` in `docs/remote-ask.md` and `peerhub/cli/guide.py`.
- **#123 (Windows upgrade locks):** Document stopping `peerhub monitor` before running `pip install -U` in `README.md` and `guide.py`.
- **#130 (Schema migration hazard):** Document irreversible schema v4 migration; mandate backup and `--db` dev copies in `README.md`, `docs/migrations.md`, and `guide.py`.
- **#134 (Sandbox limits):** Document per-peer sandbox constraints (cc workspace approvals, cx network block, ag write unconstrained) in `docs/remote-ask.md` and `guide.py`. Scoped to observed version/environment.
- **#135 (Status codes & session semantics):** Publish reference tables for JSON status codes, exit codes, and `ACTIVE`/`LOST` session states in `docs/remote-ask.md` and `README.md`.
- **#136 (Windows/PowerShell):** Provide tested examples for `ForEach-Object -Parallel` with isolated streams, quoting, and `--query-file` in `README.md` and `guide.py`.

---

### Slice S5: Release Gates & Measured CI Attribution

- **CI Duplication Policy:** All candidate-bound release gates, local G3 evidence checks, and matrix validation are strictly preserved.
- **Timing Attribution:** Avoid asserting qualitative duplication as the measured bottleneck (historical Windows Python 3.13 test step took 1959s vs 27s for dependencies). Real attribution will be measured from JUnit and step timings in the post-fix release run and posted directly to the corresponding GitHub issue.

---

## 2. Sequencing & Implementation Slices

```
[Slice S1: Execution Safety & Stream Fencing]  <-- AUTHORIZED FOR IMPLEMENTATION
  ├── #140: Native validate_execution_policy() fail-closed ag; cached replay preserved
  ├── #125: prompt_guard single-peer ask stream fencing inside append_record tx
  ├── #126: empty_response indicator + task_effects: unverified
  └── #127: Structured terminal error preservation & narration split
       │
[Slice S2: Guidance Registry, Lessons & Bare CLI]
  ├── #117: S2-A guidance registry + lessons.toml + keyword explain lookup + 6 CX fixes
  ├── #120: Process stderr decoding + ask_launch_failed
  └── #121: Bare peerhub -> guide + valid global --db creation syntax
       │
[Slice S3: Top-Level Readiness & 22nd-Century Monitor]
  ├── #145: Top-level peerhub readiness (cached default, scratch-isolated probe, <=200 tokens)
  └── #146 & #147: Sleek 22nd-century monitor (dynamic pools top, cyan accent, ASCII fallback)
       │
[Slice S4: Documentation Alignment]
  └── #118, #123, #130, #134, #135, #136 documentation updates
       │
[Slice S5: Release, Validation & CI Benchmarking]
  └── Candidate G3 gate, release run, and measured timing attribution
```

---

## 3. Ratification Status

- **`ag.effort`:** **VOTE: AGREE on all contracts and Issue #147.**
- **`cx.effort`:** **VOTE: AGREE on available-peer design for S1-S4 including Issue #147.**
- **`cc.effort`:** Unavailable (session quota exhausted until 02:20 KST; frozen stream cannot be replayed).
- **Consensus:** **Available-peer consensus achieved.** Implementation of **Slice S1 AUTHORIZED**.
