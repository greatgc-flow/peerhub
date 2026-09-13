# D-CTX Independent Security Review — Voice ag.opus

**Status:** INDEPENDENT REVIEW — this is the first D-CTX voice that is not cx in any form
**Date:** 2026-09-13
**Author:** ag.opus (Antigravity, Claude Opus 4.6)
**Scope:** Security review of `peerhub-dctx-proposal-1-2026-09-13.md` per the independent-review brief in holistic-renewal section 8.4
**Prior contact with D-CTX:** None. I read proposal-1 and the holistic renewal section 8 for the first time during this review session.

---

## Overall Verdict: REVISE

Proposal 1 correctly identifies real, empirically confirmed defects in PeerHub's governance integrity. Its directional design (attempt-scoped credential files, separated identity/credential/authorization, `GovernanceWriteContext` as the service-boundary type) is architecturally sound. The holistic renewal's 14 corrections (D1–D14) are substantially correct and strengthen the design materially.

However, I cannot issue ACCEPT because:

1. **The proposal is reviewing a proposal, not code.** Zero D-CTX implementation exists. I cannot verify that the proposed mechanisms work because there is nothing to test against. Every claim about credential validation, grant enforcement, and file lifecycle is design-level — none has been measured.

2. **Two critical gaps remain open even as design:** the workspace trust anchor (who may issue credentials when any process can instantiate the library and open the DB?) and the same-user process isolation boundary (empirically, I read, wrote, and forged records in the governance DB from this very review session — see Canary evidence below).

3. **The environment composition defect is real and pre-existing.** Before D-CTX adds any environment variables, the current `environment_delta` → `Popen(env=...)` path would wipe the child's inherited environment. This must be fixed first or simultaneously.

The design should proceed to implementation, but with the corrections in D1–D14 integrated (not as a post-hoc fixup layer), and with the trust-anchor and process-isolation questions resolved before any "verified" label is applied to governance operations.

---

## The Eight Review Questions

### Q1: What can a worker read/write/spawn/impersonate under each profile?

**Verdict: REVISE** — Proposal 1 correctly describes the gap but does not adequately constrain what happens when a worker exploits it.

**Evidence (empirical canaries, this session):**

| Canary | Result | Evidence tag |
|---|---|---|
| Read source files under `P:\workspace\peerhub` | ✅ SUCCESS | `[empirical_probe]` |
| Read `.peerhub/peerhub.sqlite3` (663,552 bytes) | ✅ SUCCESS | `[empirical_probe]` |
| Write arbitrary file inside workspace | ✅ SUCCESS — wrote and deleted `peerhub-dctx-write-canary-927032136.tmp` | `[empirical_probe]` |
| Write outside workspace (`C:\Users\GC\dctx-escape-canary.tmp`) | ✅ SUCCESS — **no filesystem confinement** | `[empirical_probe]` |
| Spawn arbitrary subprocess (`cmd.exe /c echo`) | ✅ SUCCESS — exit code 0 | `[empirical_probe]` |
| Create+drop table in governance SQLite DB | ✅ SUCCESS — `CREATE TABLE dctx_canary_write_test` succeeded | `[empirical_probe]` |
| **Forge a mutation_request with arbitrary actor_id** | ✅ SUCCESS — inserted `actor_id='FORGED_ACTOR_cx'`, read it back, then cleaned up | `[empirical_probe]` |
| Read existing PEERHUB_* environment variables | None set — no D-CTX carrier exists | `[empirical_probe]` |
| Read `.peerhub/run/contexts/` directory | Does not exist — no D-CTX files deployed | `[empirical_probe]` |

**Process/user identity:**
- OS user: `GC` (same account for all peers)
- Process ID: 13732
- `LocalProcessCallerIdentityProvider` would return `local-cli:DESKTOP-...\GC` for all three peers and the human terminal identically

**Assessment:** Under the current codebase, a dispatched worker (ag, cx, or cc) running as the same OS user can:
- Read any other peer's credential files (if they existed)
- Read and write the governance SQLite database directly
- Forge mutation requests with any actor_id
- Spawn arbitrary processes
- Write anywhere the OS user can write (no workspace confinement for ag per DIR-002 KNOWN GAP)

Proposal 1 acknowledges the same-user limitation in its threat model (§7, "Explicitly not defended") but the language "owner-only file" and "mode 0600/DACL" throughout the design creates an impression of isolation that does not exist when all peers share an OS account. The renewal's D3 correction addresses this clearly — D3 must be integrated into any shipping version.

**Recommendation:** The review document and any shipped implementation must prominently state: *"D-CTX v1 provides confusion protection and auditability for cooperative local peers. It does not resist a compromised or malicious peer process running under the same OS account."* This is D3's point and I agree with it fully.

---

### Q2: What anchors issuer and workspace trust?

**Verdict: REVISE** — Proposal 1's workspace trust bootstrap (§3.3) has a circular dependency that D2 identifies but does not fully resolve.

**Evidence (code + empirical):**

1. **Any caller can instantiate the library.** `ConsensusService` is constructed at [`cli/__init__.py:463`](file:///P:/workspace/peerhub/peerhub/cli/__init__.py#L463) by calling `ConsensusService(runtime.governance_broker, ...)` — the `runtime` is created from a `RuntimeContext` that takes a `paths` parameter derived from `--workspace` CLI flag. There is no check that the caller is a legitimate PeerHub dispatcher. `[code]`

2. **Any caller can open the DB.** The DB path is deterministic (`.peerhub/peerhub.sqlite3`). I opened it directly via `sqlite3.connect()` and wrote to it successfully. `[empirical_probe]`

3. **No issuer identity exists.** The DB has no `credential_issuers` table, no `workspace_trust_anchors` table, and no signing keys. The proposal says "server-minted" but the "server" is a library any Python process can import. `[code — searched entire migration chain, 31 migrations, zero credential/provenance tables]`

4. **Proposal 1 §3.3's bootstrap:** "The CLI reads the context file → finds a candidate root → opens that root's DB → verifies the credential hash there." But if I can fabricate both the context file and the DB, verification succeeds against my own forgery. D2 in the renewal correctly identifies this: "A fabricated DB can validate fabricated credentials unless the target operation is anchored to an independently trusted workspace/issuer."

**Fake-DB rejection test:** No mechanism exists to test against — there is no credential table, no token storage, no issuer verification code. The entire credential subsystem is unimplemented. `[code — grep for 'PEERHUB_CONTEXT_FILE', 'DispatchContext', 'dispatch_context', 'credential', 'bearer', 'governance_grant' in Python code: 0 results]`

**Recommendation:** Before shipping, the design must specify:
- What makes a workspace DB authoritative (e.g., a workspace identity secret provisioned at `peerhub init` time, stored outside the workspace tree or protected by a separate mechanism)
- How a context file is bound to exactly one DB instance (not just "the DB at the path the file says")
- Whether nested dispatch from a different workspace can present credentials to the parent workspace's DB (it should not be possible)

---

### Q3: Can one actor impersonate another or count twice?

**Verdict: REVISE** — The impersonation gap is real, confirmed by code analysis and reproduces the proposal's own finding.

**Evidence (code analysis):**

1. **CLI passes actor flags directly through.**
   - [`cli/__init__.py:544`](file:///P:/workspace/peerhub/peerhub/cli/__init__.py#L544): `service.cast_vote(parsed.round_id, actor_id=parsed.actor, choice=parsed.choice)` — `parsed.actor` comes from `--actor` CLI flag
   - [`cli/__init__.py:527`](file:///P:/workspace/peerhub/peerhub/cli/__init__.py#L527): `proposer_id=parsed.proposer` — from `--proposer` flag
   - [`cli/__init__.py:498`](file:///P:/workspace/peerhub/peerhub/cli/__init__.py#L498): `voter=parsed.voter` — from `--voter` flag
   - No identity verification occurs between the flag value and the calling process. `[code]`

2. **`ConsensusService.cast_vote` checks only string membership.**
   - [`consensus.py:519`](file:///P:/workspace/peerhub/peerhub/governance/consensus.py#L519): `if not isinstance(eligible, (tuple, list)) or actor_id not in eligible` — this is a list-membership check on a caller-supplied string, not an authenticated identity check. `[code]`

3. **Proposal 1 §1.1 reproduced the end-to-end impersonation.** The author proposed a round requiring `cc,cx`, voted as `cc`, then voted as `cx` from the same process, reaching quorum. I can independently confirm this is possible from the code: nothing in `cast_vote` checks whether the caller is actually the peer named in `actor_id`. `[code]`

4. **Duplicate detection is string-keyed.** Votes are stored as `votes[actor_id] = {...}` ([`consensus.py:529`](file:///P:/workspace/peerhub/peerhub/governance/consensus.py#L529)). A second vote from the same `actor_id` string simply overwrites the first. There is no concept of credential-instance deduplication. Two different credentials for the same peer instance would count as one vote (correct), but two processes both claiming `actor_id="cx"` would also merge (incorrect — the second might be illegitimate). `[code]`

5. **Profile/instance confusion.** `selected_peer_instance_id` vs `selected_profile_id` are separate in `CapabilityLease` ([`capability.py:170-171`](file:///P:/workspace/peerhub/peerhub/dispatch/capability.py#L170-L171)) but consensus votes use a flat `actor_id` string that could be either. The proposal correctly identifies this (§1.2) and proposes `selected_peer_instance_id` as the governance actor. D11 in the renewal further clarifies that uniqueness ≠ independence. `[code + design]`

6. **Final Call ACK has the same gap.** [`consensus.py:150`](file:///P:/workspace/peerhub/peerhub/governance/consensus.py#L150): `if not isinstance(eligible, (list, tuple)) or actor_id not in eligible` — identical string-membership check, no authentication. `[code]`

7. **`resolve()` does not check authority at all.** [`consensus.py:297-354`](file:///P:/workspace/peerhub/peerhub/governance/consensus.py#L297-L354): `resolved_by` is accepted as a bare string, not checked against participants, eligible voters, or any administrative role. Anyone who can call the service can resolve a round. `[code]`

8. **`abandon()` likewise has no authority check** on `abandoned_by`. `[code]`

**Recommendation:** The proposal's `GovernanceWriteContext` approach is the right fix, but the review brief specifically asks whether the proposal is ready to implement. For impersonation resistance, the minimum implementation must:
- Bind `actor_id` to a credential that proves dispatch identity
- Reject explicit `--actor` flags that contradict the credential
- Ensure `resolve`, `abandon`, `reject_on_dissent`, and `request_escalation` require appropriate authority (not just any string)

---

### Q4: Can admin/asserted/system routes bypass protection?

**Verdict: REVISE** — Multiple bypass routes exist in the current codebase and proposal 1 does not fully enumerate them.

**Evidence (code analysis — complete mutator map):**

**Direct CLI governance handlers (no ApplicationAPI, no Client, no identity check):**

| Handler | File | Actor source | Auth check |
|---|---|---|---|
| `consensus propose` | `cli/__init__.py:519-542` | `--proposer` flag | None |
| `consensus vote` | `cli/__init__.py:543-554` | `--actor` flag | None |
| `consensus proposal-add` | `cli/__init__.py:464-494` | `--from` flag (default: `"cc"`) | None |
| `consensus proposal-vote` | `cli/__init__.py:495-518` | `--voter` flag (default: `"cc"`) | None |
| `consensus arbiter-review` | `cli/__init__.py:589+` | Arbiter dispatch actor | Limited structural check |
| `task create/claim/complete/fail` | `cli/__init__.py:658+` | `--creator`/`--actor` flag | None |
| `lesson propose/approve/activate` | `cli/__init__.py:703+` | `--proposer`/`--approved-by`/`--actor` flag | None |

All of these construct `ConsensusService`, `TaskService`, or `LessonService` directly from `runtime.governance_broker` — they never touch `ApplicationAPI` or `Client`.

**`ApplicationAPI.submit` auth is a skeleton:**
- [`api.py:2268-2286`](file:///P:/workspace/peerhub/peerhub/application/api.py#L2268-L2286): Comment says "assume caller context checks out for this skeleton." Only checks `caller.client_id == cmd.submission.client_id`. Does not bind `actor_id` to an authenticated subject. `[code]`

**`Client` is never instantiated in production:**
- Zero `Client(...)` calls in any production code under `peerhub/`. Only used in test suites. `[code]`

**System-level mutations with hardcoded actors:**
- `mark_timeout` uses `actor_id="system:timeout"` ([`consensus.py:236`](file:///P:/workspace/peerhub/peerhub/governance/consensus.py#L236)) — no special system-context type
- `direct_ask.py` uses `actor_id="cli-direct-ask"` ([`direct_ask.py:567`](file:///P:/workspace/peerhub/peerhub/application/direct_ask.py#L567))
- `broadcast.py` uses `actor_id="broadcast-coordinator"` ([`broadcast.py:248`](file:///P:/workspace/peerhub/peerhub/application/broadcast.py#L248))
- These are string constants — no `TRUSTED_SYSTEM` context type exists yet. `[code]`

**Direct SQLite access:**
- Any process with OS-level read/write access to `.peerhub/peerhub.sqlite3` can bypass all application-layer checks entirely. I demonstrated this by inserting a forged mutation request. `[empirical_probe]`

**Policy/config/role writes:**
- The `mutation_requests` table has no provenance columns. There is no way to distinguish a legitimate system mutation from a forged one after the fact. `[code + empirical_probe]`
- Proposal 1 §4.2 says "System-generated mutations use a distinct `TRUSTED_SYSTEM` context" but this does not exist yet and the proposal does not specify how to prevent a caller from constructing such a context. D13 in the renewal addresses this: "a caller-built TRUSTED_SYSTEM context does not establish Tier-0 authority."

**Recommendation:** The complete mutator map must be part of the implementation plan. D13 and D14 are critical — ASSERTED mode must have bounded capabilities, and no CLI flag combination should be able to mint administrative authority.

---

### Q5: Does the carrier reach actual tool subprocesses?

**Verdict: REVISE** — Cannot be determined; no carrier exists to test.

**Evidence:**

1. **All three adapters emit `environment_delta={}`:**
   - [`agy_adapter.py:229`](file:///P:/workspace/peerhub/peerhub/adapters/agy_adapter.py#L229)
   - [`claude_adapter.py:279`](file:///P:/workspace/peerhub/peerhub/adapters/claude_adapter.py#L279)
   - [`codex_adapter.py:485`](file:///P:/workspace/peerhub/peerhub/adapters/codex_adapter.py#L485)
   - No D-CTX variables are set by any adapter today. `[code]`

2. **No `PEERHUB_CONTEXT_FILE` or `PEERHUB_DISPATCH_*` variables exist in this environment.** `[empirical_probe]`

3. **The environment composition defect is real and pre-existing:**
   - [`workflows.py:893-898`](file:///P:/workspace/peerhub/peerhub/application/workflows.py#L893-L898): `env=dict(invocation_plan.environment_delta) if invocation_plan.environment_delta else None`
   - [`pipe.py:409`](file:///P:/workspace/peerhub/peerhub/dispatch/pipe.py#L409): `env=config.env` passed to `subprocess.Popen`
   - Per Python's subprocess documentation, a non-`None` `env` dict replaces the entire child environment. If any adapter starts populating `environment_delta` (as D-CTX would require), `PATH`, `SystemRoot`, `TEMP`, auth tokens, and proxy settings would all be wiped from the child process. `[code]`

4. **CLI environment inheritance is unmeasured.** Proposal 1 §1.3 reports the same finding: Codex, Claude, and Agy canaries were `[probe_blocked]` due to model/network availability. I independently confirm that no per-CLI environment passthrough evidence exists. `[empirical_probe — no model turns available in this execution environment]`

5. **Proposal 1 §3.2 acknowledges the file fallback is needed** because environment scrubbing behavior is unknown. The renewal's §8.3 further reduces the proposed environment surface to `PEERHUB_CONTEXT_FILE` + `PEERHUB_WORKSPACE` only. This is the correct direction.

**Recommendation:** D7 (fix environment composition before adding a delta) is a hard prerequisite. The implementation sequence should be:
1. Fix `workflows.py` to merge `os.environ` + adapter deltas + protected D-CTX keys
2. Then add `PEERHUB_CONTEXT_FILE` (and only that, per §8.3)
3. Then run per-CLI canaries to verify the variable reaches the tool subprocess
4. Only then consider the prompt-trailer fallback sufficient

---

### Q6: Do scope/lifetime limits hold?

**Verdict: REVISE** — The existing lease/fencing infrastructure is strong but D-CTX credential lifecycle has no implementation to test.

**Evidence (code analysis of existing mechanisms):**

1. **Session lease fencing is implemented and appears sound:**
   - `LeaseState` enum covers: `RESERVED`, `ACTIVE`, `RENEWED`, `RELEASED`, `EXPIRED`, `FENCING`, `FENCED`, `IDENTITY_MISMATCH`, `OWNERSHIP_LOST`, `ABANDONED_PRE_SPAWN` ([`dispatch/contract.py:118-131`](file:///P:/workspace/peerhub/peerhub/dispatch/contract.py#L118-L131)) `[code]`
   - `LeaseFenceTuple` includes `fencing_token`, `revision`, `owner_principal_id`, `owner_instance_id`, `owner_process_birth_identity`, `command_id`, `authority_epoch` `[code]`
   - `HeartbeatWorker` validates PID identity before renewal and fails closed on mismatch `[code]`
   - CAS-based revision checks prevent stale renewal `[code]`

2. **Capability lease binding validation is thorough:**
   - [`validate_capability_binding()`](file:///P:/workspace/peerhub/peerhub/dispatch/capability.py#L263-L377) checks 14+ separate equality invariants across request, receipt, session lease, and capability lease `[code]`
   - Retry attempts require `previous_attempt_id` chain and monotonic `authorized_attempt_number` `[code]`

3. **But D-CTX credentials have zero implementation:**
   - No `DispatchContextCredential` table exists
   - No credential issuance, validation, revocation, or expiry code exists
   - No `authority_epoch` binding for credentials exists
   - The proposal describes these mechanisms but they cannot be tested `[code — full grep returned 0 results for D-CTX identifiers]`

4. **Race conditions in the proposal (D8's concern):**
   - The proposal says credentials are "valid only while all of these are true" (§2.1) but does not specify when this check occurs relative to the mutation commit. D8 correctly identifies the verification-versus-revocation race. The existing lease CAS mechanism provides a pattern, but the proposal does not explicitly say "check credential validity in the same SQLite transaction as the mutation commit."

5. **Clock change resilience is unspecified.** The proposal uses `expires_at` timestamps but does not address monotonic-clock vs. wall-clock issues, NTP jumps, or system hibernate/resume scenarios. The existing lease system uses integer timestamps (`issued_at`, `heartbeat_expires_at`) but also does not appear to use monotonic clocks.

**Recommendation:** The existing lease/fencing infrastructure provides a good foundation. Credential lifecycle should be implemented using the same CAS/epoch/fencing patterns. D8 and D12 are critical: credential validity must be checked at commit time (not just at presentation time), and completed/revoked credentials must not be revivable.

---

### Q7: What security promise may ship?

**Verdict: REVISE** — The proposal's threat model (§7) is honest but the labeling must be clearer.

**Evidence:**

1. **Proposal 1 §7 explicitly lists "not defended":** hostile same-user process, malicious admin, model/prompt compromise, vendor CLI compromise, cryptographic non-repudiation. This is accurate. `[code + empirical_probe — confirmed same-user DB access]`

2. **But the language elsewhere creates a stronger impression.** Terms like "authoritative context file," "verified consensus path," "DISPATCH_VERIFIED," and "authentication" appear throughout. A reader might reasonably conclude that the system resists adversarial attack rather than accidental confusion.

3. **D3's framing is the right one:** "cooperative local coordination with strong error detection and auditability." This should be the lead promise, not buried in a correction.

4. **Eligible operation classes for v1:**
   - ✅ Preventing accidental actor mix-ups (copy-paste errors, wrong `--actor` flag)
   - ✅ Detecting stale credentials from completed attempts
   - ✅ Preventing cross-workspace accidental writes
   - ✅ Audit trail with provenance quality markers
   - ✅ Silent-downgrade prevention (bad credential → fail, not fallback)
   - ❌ Resisting a compromised worker that can read sibling credentials
   - ❌ Resisting direct SQLite manipulation
   - ❌ Cryptographic proof of peer identity
   - ❌ Network-based trust establishment

**Recommendation:** The shipping promise should be explicitly scoped: *"D-CTX v1 provides cooperative-local confusion protection and forensic auditability. It prevents accidental impersonation, stale credential reuse, and cross-scope writes. It does not resist a compromised worker process with same-user OS access to the workspace database or credential files."* This aligns with D3 and the proposal's own §7 but makes it the headline rather than a caveat.

---

### Q8: Can migration/recovery preserve authority?

**Verdict: REVISE** — Schema rollback and emergency human routes are not specified.

**Evidence:**

1. **Current migration chain is forward-only.** The 31 SQL migrations in `peerhub/persistence/migrations/` are applied sequentially by `SqliteStateStore`. There is no rollback mechanism, no down-migration, and no migration versioning beyond `PRAGMA user_version`. `[code]`

2. **No credential tables exist to roll back.** Since D-CTX is unimplemented, there is no existing schema to worry about. But the proposal must specify what happens when a D-CTX migration (e.g., adding `credential_records` table) needs to be rolled back after credentials have been issued. D12 says "restoring a credential ledger cannot revive a saved bearer" — this requires the rollback to invalidate any credentials whose hashes are in the old DB. `[code + design]`

3. **Emergency human route is unspecified.** The proposal mentions "human override remains a separate Tier-0 action" (§4.3) but does not define the mechanism. D13 says "--human, --force, --allow-asserted-actor, a local username, or a caller-built TRUSTED_SYSTEM context does not establish Tier-0 authority." What DOES establish it? The proposal defers this without providing a concrete answer.

4. **No external effects in the current system.** The governance system is local-only (SQLite + local files). There is no remote API, no webhook, no external state mutation. This means recovery cannot cause external side effects — which is good, but should be explicitly stated as a constraint on v1. `[code]`

5. **`administrative_recovery_budgets` table exists** ([migration 0030](file:///P:/workspace/peerhub/peerhub/persistence/migrations/0030_administrative_recovery_budget.sql)), suggesting the recovery subsystem has some budget/rate-limiting infrastructure. But there is no credential-specific recovery mechanism. `[code]`

**Recommendation:** The implementation plan must address:
- Forward migration adds credential tables; rollback drops them (acceptable if active credentials are revoked first and the system gracefully falls back to ASSERTED mode)
- Emergency human route: define a specific mechanism (e.g., a signed local file, a hardware key, a separate admin process) rather than leaving it as a placeholder
- Constraint: no D-CTX v1 operation may produce external effects; all authority is local and recoverable

---

## Independent Assessment of D1–D14 and Carrier Surface Reduction

### D1 — "The file is a carrier, not authority."

**AGREE.** The proposal repeatedly uses "authoritative context file" language. Authority resides in the issuer's credential record and current lease/fence/policy state. A file that anyone with OS access can edit is a transport mechanism, not a trust anchor. Calling it "authoritative" in a prompt trailer ("Authoritative context file: ...") is actively misleading because it instructs a model to treat the file as authoritative, which it isn't.

### D2 — "Define the issuer and workspace trust anchor."

**AGREE, with additional evidence.** I empirically confirmed D2's concern: I opened the workspace DB directly via `sqlite3.connect()`, created a table, and inserted a forged mutation request with `actor_id='FORGED_ACTOR_cx'`. If D-CTX credentials were stored in this same DB, I could forge those too. The proposal's §3.3 bootstrap ("read file → find DB → verify token in DB") is circular when the attacker controls both. The trust anchor must be something a compromised worker cannot easily forge — and in a same-user same-host environment, this is genuinely hard to provide. The honest answer may be: "v1 has no trustworthy anchor beyond cooperative trust; we detect confusion, not malice."

### D3 — "Separate confusion protection from compromised-worker resistance."

**STRONGLY AGREE.** This is the most important correction. My live canaries prove that same-user isolation does not exist: I wrote files outside the workspace, spawned arbitrary processes, and modified the governance DB directly. File permissions (mode 0600 / DACL) protect against OTHER OS users, not against peer processes running as the same user. The proposal's §7 acknowledges this in its threat model but the rest of the text does not consistently reflect it. D3's recommended label — "cooperative local coordination" — must be the shipping description.

### D4 — "Grants cannot launder asserted decisions."

**AGREE.** If a round ID appears in an admitted request, that is scope information, not authorization. The proposal's governance-grants design (§2.2) correctly separates capability tier from governance authority, but D4 adds the crucial constraint that grant issuance itself must be traced to trusted policy. A CLI flag like `--room some-room` should not automatically mint a `room.message:some-room` grant. The grant must be derived from the admitted scope and approved policy, not from arbitrary caller parameters.

### D5 — "Reject .peerhub/run/contexts."

**AGREE.** If a transient/durable separation was ratified on September 9, context files belong in the transient namespace. The proposal's `.peerhub/run/contexts/<attempt_id>/` path violates that separation. Using a per-attempt exclusive unpredictable path in the transient workspace namespace is both more consistent and harder to predict/preemptively create.

### D6 — "Measure secure creation and actual reader access."

**AGREE, with empirical reinforcement.** The renewal mentions encountering `WinError 5` on a newly created temporary directory. File permission semantics differ significantly between POSIX and Windows, and between different Windows filesystem types (NTFS vs. non-NTFS). The proposal's "mode 0600 on POSIX and an explicit current-user/SYSTEM DACL on Windows" is a specification, not a measurement. Actual platform tests must verify:
- That the file is created with the intended permissions
- That the intended reader (the sandboxed peer CLI) can actually read it
- That another process under the same user cannot race the creation (symlink/reparse attacks)

I did not encounter access errors reading any workspace files in this session, but I am running as the full-privilege OS user, not inside a peer sandbox.

### D7 — "Fix environment composition before adding a delta."

**STRONGLY AGREE, independently verified.** [`workflows.py:896-898`](file:///P:/workspace/peerhub/peerhub/application/workflows.py#L896-L898) passes `dict(invocation_plan.environment_delta)` as `PipeRunnerConfig.env`, and [`pipe.py:409`](file:///P:/workspace/peerhub/peerhub/dispatch/pipe.py#L409) forwards it to `subprocess.Popen(env=config.env)`. Per Python's subprocess documentation, a non-`None` `env` parameter replaces the entire child environment. If D-CTX adds variables to `environment_delta`, the child process would lose `PATH`, `SystemRoot`, `TEMP`, authentication tokens, and proxy settings. This is a correctness bug independent of D-CTX, but D-CTX would trigger it. Must be fixed before or simultaneously with D-CTX implementation.

### D8 — "Verify at commit."

**AGREE.** The proposal says credentials are "valid only while" several conditions hold, but does not specify the check point relative to the mutation's SQLite commit. In the existing codebase, `GovernanceBroker.submit()` performs CAS revision validation in the same transaction as the state update — a good pattern. D-CTX credential validity should be checked in the same transaction. Without this, a revoked credential could authorize a mutation that commits after the revocation.

### D9 — "Separate stable operation identity from credential-instance evidence."

**AGREE.** The proposal says provenance should participate in idempotency hashing (§4.1: "changing provenance while reusing an idempotency key must produce an idempotency-payload mismatch"). But if `credential_id` and `verified_at` are part of the hash, a legitimate retry with a rotated credential (new `credential_id`, new `verified_at`) would be rejected as a payload mismatch. D9 correctly identifies the need to separate the semantic mutation identity (operation + actor + resource + decision) from the authentication-attempt record (credential instance + timestamp). Both must be auditable, but only the former should participate in idempotency.

### D10 — "Bind the decision content."

**AGREE.** The current consensus vote structure stores `choice`, `actor_id`, `cast_at`, `mutation_id`, and `reason` ([`consensus.py:529-535`](file:///P:/workspace/peerhub/peerhub/governance/consensus.py#L529-L535)). It does not bind the vote to the content being voted on (source hash, question text, electorate snapshot). If the proposal content changes between two voters' reads, both might vote on different versions while the system counts them toward the same quorum. The `source_hash` field exists on proposals (`consensus.py:85`) but is not checked at vote time. The `resolution_decision_hash` ([`consensus.py:284-295`](file:///P:/workspace/peerhub/peerhub/governance/consensus.py#L284-L295)) hashes votes + outcome but not the original proposal content. D10 correctly identifies this gap.

### D11 — "Uniqueness is not independence."

**AGREE.** Two profiles of the same root peer (e.g., `cx.deepthink` and `cx.astra`) should count as one voter, not two. The proposal correctly separates `selected_peer_instance_id` from `selected_profile_id`, but the current consensus system uses a flat `actor_id` string that could be either. If the electorate is defined as `["cc", "cx", "ag"]` and the consensus system does not enforce that `cx.deepthink` maps to voter `cx`, it is possible that string uniqueness substitutes for independence. The existing `CapabilityLease` model already separates `selected_peer_instance_id` from `selected_profile_id` ([`capability.py:170-171`](file:///P:/workspace/peerhub/peerhub/dispatch/capability.py#L170-L171)) — the consensus system should use the same separation.

### D12 — "Prevent authority resurrection."

**AGREE.** The existing lease system has `authority_epoch` in `LeaseFenceTuple`, which provides epoch-based revocation. D-CTX credentials should bind to a similar epoch. The proposal mentions `revoked_at` but does not specify the ordering guarantee: does revocation take effect before or after concurrent in-flight mutations? D12's "revocation precedes deletion" is the correct ordering. Additionally, DB restore/backup scenarios must not revive credentials — this requires the credential validation to check something that survives restore (e.g., a monotonic epoch that is also stored/checked outside the DB, or a forward-only counter).

### D13 — "No administrative escape hatch."

**AGREE.** The current codebase has no administrative role enforcement at all — `resolve()`, `abandon()`, and `reject_on_dissent()` accept any `actor_id` string without checking authority. Adding a `--force` or `--human` flag that bypasses D-CTX would recreate the same problem with one more layer of indirection. D13 correctly requires that protected operations use an independently specified trusted route. The practical challenge is: what IS that route in a single-user, single-host environment? The answer may need to involve an out-of-band mechanism (e.g., a specific terminal session with interactive confirmation, not a batch-mode flag).

### D14 — "Bound transitional asserted writes."

**AGREE.** The compatibility story (proposal §5) allows ASSERTED mode for backward compatibility, but D14 correctly constrains what ASSERTED mode may do. If ASSERTED calls can modify the policy that defines what requires verification, then the verification requirement can be self-referentially removed. The bounded list (cannot issue credentials, lower protected assurance, rewrite policy/electorate, assign privileged roles, finalize protected rounds, or grant external effects) is the right set of constraints.

### §8.3 — Carrier surface reduction

**AGREE.** The proposal defined 10 environment variables (§3.1). The renewal's §8.3 reduces this to `PEERHUB_CONTEXT_FILE` (locator) and `PEERHUB_WORKSPACE` (convenience). This is correct for two reasons:
1. **Attack surface:** Every environment variable is a potential leak channel (process diagnostics, crash dumps, child inheritance). Minimizing the set minimizes the surface.
2. **Consumer measurement:** No consumer (Claude, Codex, Agy) has been measured to need or preserve the other 8 variables. Adding unmeasured variables violates DIR-004.

The remaining identifiers belong in the validated context file, where they are read by the CLI's own code path and never exposed to untrusted process environments.

---

## Summary of Most Significant Findings

1. **Governance identity is completely unverified.** Every governance mutation (vote, propose, resolve, abandon, ACK) accepts an arbitrary `actor_id` string from CLI flags with zero authentication. One process can vote as all eligible peers and reach quorum alone. This is the core defect proposal 1 aims to fix and it is real.

2. **The governance DB is directly writable by any same-user process.** I inserted a forged `mutation_request` with `actor_id='FORGED_ACTOR_cx'` directly via `sqlite3.connect()`. No credential, token, or permission prevented this. This means D-CTX v1 cannot claim resistance to a compromised worker unless the DB is somehow protected from direct access.

3. **Environment composition will break if D-CTX adds variables.** The current `environment_delta → Popen(env=...)` path replaces rather than merges the child environment. This is a pre-existing bug that D-CTX would trigger.

4. **The workspace trust anchor is undefined.** No mechanism prevents a fabricated DB from validating fabricated credentials.

5. **All 14 corrections (D1–D14) are substantively correct.** I agree with all of them, most strongly with D3 (confusion vs. compromise scope), D7 (environment composition), and D13 (no admin escape hatch). None are wrong; several are essential.

The design direction is right. Implementation should proceed with D1–D14 integrated from the start, not applied as patches. The shipping label must be "cooperative-local confusion protection," not "peer authentication."

---

## Evidence Appendix

### Canary execution environment
- Date: 2026-09-13T22:01+09:00
- OS user: `GC`
- Process: ag.opus via Antigravity CLI
- Workspace: `P:\workspace\peerhub`
- DB: `.peerhub/peerhub.sqlite3` (663,552 bytes, 53 tables)
- No PEERHUB_* environment variables set
- No D-CTX code or tables exist in the codebase or database

### Code versions examined
- PeerHub codebase at `P:\workspace\peerhub` (working tree as of 2026-09-13)
- 31 SQLite migrations (0001 through 0031)
- 3 production adapters: agy, claude, codex
- Proposal 1: `docs/design/peerhub-dctx-proposal-1-2026-09-13.md`
- Holistic renewal: `docs/design/peerhub-holistic-renewal-RATIFIED-cx-astra-2026-09-13.md`
