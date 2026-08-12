# PeerHub multi-peer broadcast / consensus design (2026-08-11)

Status: **ratified through three dialectical review rounds and closed by
a validated prototype.** Migration `0020_broadcast_correlation` and its
7-test harness landed in commit `8650314`; see Section 8. (Header
corrected 2026-08-11 — it previously read "draft 3 … No code written,"
which stopped being true when `0020` landed.)

All three rounds endorsed the direction (the Primitive A / Primitive B
split) and found errors in the implementation reasoning: seven in round
1, six in round 2, two in round 3. All fifteen are addressed;
**Section 7** has the disposition tables.

What remains unbuilt for Primitive A is the coordinator itself —
`BroadcastCoordinator.fan_out()`, disposition computation, deadline
closing, and partial-failure semantics. The prototype validated the
schema and the admission/idempotency identity path only.

Draft 3's headline change is a **descope**. Durable response transcripts
are removed from Primitive A's initial increment and deferred to their
own separately-ratified increment, because draft 2's version gave
broadcast a stronger durability guarantee than the dispatch path it
wraps — inverting the layering Section 3.1 exists to protect. Primitive A
is now: one coordinator, two small correlation tables, sequential
fan-out. Draft 1 proposed the most; draft 3 builds the least, and every
reduction traces to a verified defect rather than to scope fatigue.

Scope: the Phase 3 gap recorded in
`HUB-REPLACEMENT-ROADMAP-2026-08-09.md` — hub.py has actively-used
multi-peer coordination primitives (`ask-all`, `consensus-propose`/`-vote`/
`-check`/`-sweep`, `ask-coordinator`, room/thread messaging) with no
peerhub equivalent.

Reading order note: Section 1 is the load-bearing part. It concludes that
most of hub.py's consensus formality was **not** exercised by this
session's actual usage, and Section 5 declines to build it now. If you
only read one section, read Section 1's measurements.

---

## Section 0 — Evidence base and source tags

Per DIR-004, every claim below carries a source. Measurements were taken
2026-08-11 on this machine.

| Tag | Meaning |
| --- | --- |
| `empirical_probe` | Measured directly this session; command shown |
| `source_read` | Read from the cited file at the cited line |
| `TEST NEEDED` | No evidence exists; explicitly not estimated |

Sources read for this design: `P:\_sys\core\hub.py` (lines 7563-8010),
`P:\_sys\ai\protocol.json`, `P:\_sys\docs-v2\general\protocol.md`
§4.1-4.7, `P:\_sys\claude\config\CLAUDE.md` R:6-10 trigger rules, and
peerhub's `adapters/contract.py`, `application/workflows.py`,
`dispatch/service.py`, `routing/contract.py`.

---

## Section 1 — What this session actually needed

### 1.1 What the dialectical rounds actually did

Tonight's Alembic increment-2 ratification ran as: one question dispatched
independently to `ag.deepthink`, `cx.deepthink`, and `cc.deepthink`
(round 1, each peer told explicitly it was one of N independent voices and
not to self-orchestrate); then a synthesized position broadcast back for
cross-critique (round 2); then terminal synthesis and user ratification.

That is a **two-wave fan-out with correlation**, and nothing more.

### 1.2 The formal machinery was not used — measured, not inferred

`empirical_probe`, enumerating `.ai/consensus/*.json`:

```
real round files: 65
rounds per month: {'2026-06': 4, '2026-07': 61}
outcomes: {'unanimous': 50, 'timeout': 10, 'human_gate': 2,
           'disagree': 2, 'supermajority': 1}
statuses: {'finalized': 51, 'escalated': 12, 'rejected': 2}
```

**Zero consensus rounds exist for August 2026.** The most recent is
`r-a551`, proposed `2026-07-28T23:07:32`. Tonight's Alembic decision —
squarely inside DIR-006's "architectural decision requires unanimous
agreement" scope, and inside
`protocol.json.consensus.r10_requires_finalized_for`'s
`governed_decisions` — opened **no formal round at all**. It was ratified
socially (three independent positions plus a user decision), not
mechanically.

### 1.3 The round file is a ratification receipt, not a deliberation mechanism

`empirical_probe`, timestamp analysis across the 54 rounds carrying two or
more timestamped votes:

```
vote-timestamp spread (first -> last vote), seconds:
  min=0  median=25  max=284872
  rounds where ALL votes landed within 2s of each other: 24/54 (44%)
propose -> first vote, seconds: median=15
```

A real peer dispatch takes minutes; tonight's `cc.deepthink` legs each ran
for several. Yet the median round collects its first vote 15 seconds after
proposal and closes 25 seconds later, and 44% of rounds have all votes
landing inside a two-second window — i.e. written by one actor in one
batch.

The conclusion this forces: in practice, `consensus-propose`/`-vote` is
**a durable record written after deliberation concluded elsewhere**. The
deliberation itself happens through `ask`/`ask-all`. The voting subsystem
is doing bookkeeping, not decision-making.

This matters for the design because it means broadcast and ratification
are separable concerns that hub.py has fused, and peerhub does not have to
fuse them.

### 1.4 The dominant failure mode is neither disagreement nor escalation

Ten of 65 rounds (15%) closed by sweep `timeout` — five times the
`disagree` count (2) and five times `human_gate` (2). The most common way
a round ends abnormally is that somebody opened it and nobody finished it.
`empirical_probe`, from the outcome histogram above.

A design that makes it easy to open a round and easy to leave one dangling
reproduces that. Section 5's decision is partly informed by this.

### 1.5 Honest answer to the question as posed

**The simple version would have covered tonight's actual usage.** Of
hub.py's consensus formality, the following did not fire even once
tonight, and would not have fired had the machinery been available:

- quorum snapshots (`hub.py:7719-7729`)
- RED-voter health-eligibility filtering (`hub.py:7711-7718`)
- `MAX_ROUNDS=3` rejection escalation (`hub.py:7677`, `7694-7697`)
- vote immutability / `VOTE_ALREADY_CAST` (`hub.py:7894-7900`)
- the sandbox broker vote-merge path (`hub.py:7908-7914`)
- mid-round gate-closure escalation (`_decide_consensus`, `hub.py:7748`)

What *was* load-bearing tonight, and has no peerhub equivalent:

1. Fan-out of one prompt to N targets with **per-target isolation**.
2. **Correlation** of N responses back to one question.
3. A **second wave** carrying a synthesis derived from wave one.
4. A durable record that the deliberation happened and what it produced.

Item 1 is not a nicety. The roadmap records that this session hit a real
defect — an IPC query file accidentally reused across two different peer
targets, caught only because the second dispatch failed loudly. That is a
fan-out hygiene failure, and CLAUDE.md's *Peer Dispatch Safety* section
already flags file reuse as carrying a measured ~45x zombie rate (55.6%
vs 2.7%) — a hub-ecosystem measurement, `source_read`, not re-verified
here. A broadcast primitive whose contract accepts prompt *content* and
never a caller-owned query-file path eliminates the class structurally,
because there is then no file to reuse. (Draft 2 said "its own
materialized prompt" here; that was the mechanism round 1 disproved, and
Section 3.1.1 carries the corrected version.)

I am not going to argue for building the voting subsystem on the strength
of tonight's usage, because tonight's usage does not support it.
Section 2 asks the different question that does.

---

## Section 2 — What the protocol would require if peerhub enforced it

Section 1 asked what was used. This section asks what
`collab_rate`-driven behavior would *demand* formal semantics if peerhub
ever becomes the enforcement point for the protocol hub.py enforces
today. These are different questions and they have different answers.

`protocol.json.collab_rate.current = 10` (`source_read`), and
`protocol.json.consensus.r10_voters = ["cc","ag","cx"]`.

### 2.1 The R:6-10 trigger rules, mapped to required primitives

| Rule | Requirement (CLAUDE.md, `source_read`) | Primitive actually needed |
| --- | --- | --- |
| R:6+ | 2nd consecutive error → send logs to any peer | 1-to-1 ask — **already Phase 3 core** |
| R:7+ | Ambiguous options (≥2) → request trade-off analysis from peers | **Broadcast + collect.** No voting |
| R:8+ | Sub-task completion → intermediate check | Broadcast + collect |
| R:9+ | 5 consecutive Grep/Read → validate context sufficiency | 1-to-1 ask |
| R:10 | Final Audit: report only after **unanimous consensus** | **Voting.** Nothing weaker suffices |

Four of the five rules need no voting whatsoever. R:7+ and R:8+ — the two
that are genuinely multi-peer — need exactly the broadcast primitive and
nothing else.

### 2.2 What genuinely cannot be expressed without a voting record

R:10 plus protocol.md §4.4 impose requirements that a broadcast primitive
structurally cannot satisfy (`source_read`, protocol.md §4.4):

1. **Explicit per-voter agreement.** "Every gate-OPEN registered voter
   MUST explicitly `agree` before FINALIZE." A collected response is not a
   vote; agreement has to be a distinct, recorded act.
2. **Absence ≠ approval.** "Offline auto-abstain does NOT satisfy
   agreement; a gate-OPEN required voter that goes offline mid-round with
   no prior `agree` blocks finalization." Distinguishing *didn't answer*
   from *abstained* from *agreed* requires a durable per-voter slot with
   three states. A response collection has two (present/absent).
3. **A frozen quorum denominator.** "Gate state is round-scoped. Snapshot
   captured at round-start. Gate closure after snapshot does NOT change
   N." This requires the eligible-voter set to be committed at a point in
   time and never re-derived.
4. **Non-proposer requirement.** "At least one voter from a distinct
   failure domain from the proposer MUST actively `agree`. Proposer MUST
   NOT self-finalize." Requires knowing who proposed and who agreed, as
   distinct recorded facts.
5. **A defined close moment.** "Retroactive veto: NONE for procedurally
   valid rounds." Only meaningful if there is an identifiable instant at
   which the round closed.

And `protocol.json.consensus.r10_requires_finalized_for` names the scope
where a *finalized round* is mandatory: `governed_decisions`,
`side_effecting_implementation`, `protocol_edits`, `config_edits`.

So: **if peerhub is ever meant to enforce the same protocol, the voting
primitive is not optional.** Section 1's finding is that it isn't needed
for what peerhub does *today* — not that it is never needed.

### 2.3 hub.py-specific plumbing that should NOT be replicated

Everything in this list exists because of hub.py's execution model, not
because the protocol requires it.

- **JSON-file-per-round with `.tmp` sidecars and advisory lock files.**
  peerhub has SQLite, WAL, and a read/write UnitOfWork split. Concrete
  evidence that the file scheme leaks: the consensus directory still
  contains orphaned atomic-write temporaries, e.g.
  `r-ec82.json.74f502e0.tmp` (dated Jul 4) and
  `r-f087.json.a8b8fbff.tmp`, from writes that never completed
  (`empirical_probe`, directory listing). A transactional store removes
  this failure class rather than mitigating it.
- **The sandbox broker vote-merge path** (`_apply_vote_merge`,
  `_queue_vote_merge`, `hub.py:7802-7850`). This exists solely because
  hub.py peers run under differing sandbox policies and some cannot
  rename files (`SandboxRenameDeniedError`). peerhub dispatches through
  one process owning one database; there is nothing to broker.
- **`is_routable` / `_healthy_peer` reading per-peer `health.json`
  files** (`hub.py:424`, `2598`). peerhub has its own `HealthService`,
  admission snapshots, and circuit state. A peerhub round must use
  peerhub's own health evidence, not read hub's files — otherwise the two
  systems disagree about who is eligible.
- **`_maybe_run_arbiter_on_finalize`** spawning a ~300s subprocess
  outside the round lock. DIR-005's arbiter is a separate ratified
  mechanism, terminal-applied per LL-20260703-005; it is not part of a
  consensus primitive.
- **`_emit_decision_capsule`** (`hub.py:7923`) for DocsSyncer — hub
  ecosystem integration.
- **`MAX_ROUNDS` counted by globbing files and string-matching
  `subject`** (`hub.py:7681-7692`). Fragile by construction; in peerhub
  this is a foreign key.
- **stdout formatting** (`action_consensus_check`, the `━` separators in
  `action_ask_all`). CLI presentation, not domain logic.

---

## Section 3 — Primitive A: `BroadcastDispatch` (recommended, build first)

This is the primitive Section 1 justifies on measured usage.

### 3.1 Core principle: a broadcast is N ordinary dispatches, not a new path

The single most important design decision here is that **broadcast adds a
coordinator above the existing dispatch path and changes nothing inside
it.** Each leg is a full, ordinary dispatch:

- its own `CommandID`
- its own admission and `RouteDecision`
- its own `CapabilityLease`, issued and validated by the existing
  `AdmissionCoordinator`
- its own `dispatch_attempts` row
- its own `require_dispatch_capability()` pre-spawn gate

"Dispatch" here means the **full** per-leg pipeline — admission, prepare,
then execute — not a bare `dispatch_and_execute()` call, which requires
an already-admitted command. Section 6 step 3 spells out all seven steps.

Rationale: the capability-lease work (errata Sections 7.2-7.4,
implemented through `ad56938`/`e8f7745`) placed the enforcement gate
inside `ApplicationWorkflows.dispatch_and_execute`
(`application/workflows.py:540`). A broadcast primitive that assembled its
own invocation path would sit *beside* that gate and silently reacquire
every hazard the capability work closed. Reusing the path means broadcast
inherits enforcement rather than re-implementing it, and the security
tests already written continue to cover the fan-out case unchanged.

#### 3.1.1 Why the IPC-file-reuse defect cannot recur — corrected mechanism

> **Round-1 correction (cx, independently re-verified by the terminal and
> by me against source).** Draft 1 claimed "each leg materializes its own
> prompt artifact under its own `attempt_id` through the existing
> `ArtifactMaterializer`, so two legs physically cannot share a prompt
> file." **That mechanism does not exist.** All three real adapters read
> `request.prompt_content` and inline it directly into `argv`, returning
> `artifacts=()` and `stdin_payload=None`:
> `agy_adapter.py:137,146`, `claude_adapter.py:144,153`,
> `codex_adapter.py:155,164` (`source_read`). No prompt file is
> materialized at all today, so no per-leg prompt artifact can be the
> thing that prevents sharing one.

The conclusion survives; the reason is different, and simpler.

The defect this session hit was a *caller-owned query file path* reused
across two peer targets — the hub.py IPC pattern, where the caller writes
`P:\_sys\ai\ipc\{peer}-{ts}-{rand}.txt` and passes `--query-file`. The
peerhub broadcast contract removes the category:

- `fan_out()` accepts **prompt content**, never a caller-supplied file
  path. There is no `--query-file` equivalent in the contract.
- Each leg builds its own `AdapterRequest` with
  `prompt_content=<the round's prompt>` and `prompt_reference=None`,
  exactly as `direct_ask.py:245-252` does today (`source_read`).
- The adapter inlines that string into its own `argv` tuple.

So there is no shared file to reuse, because there is no file. The
property holds by *absence of a filesystem handoff*, not by per-leg
materialization.

One dependency to record honestly: `AdapterRequest.prompt_reference`
exists in the contract (`adapters/contract.py:305`) and is unused by all
three real adapters. If a future adapter starts honoring
`prompt_reference`, this property stops being free and the broadcast
coordinator must own reference generation per leg. That is a constraint
on future adapter work, and it belongs in the acceptance criteria
(Section 6), not as an assumption buried here.

### 3.2 Proposed durable state

**Correlation only.** Draft 1 said "correlation, not content"; draft 2
extended it to carry response content; **draft 3 returns to
correlation-only and defers response durability to its own increment**
(Section 3.3). This table records *which legs exist, what state they are
in, and which command each maps to* — nothing about what a peer said.

```
broadcast_rounds
  broadcast_round_id   TEXT PRIMARY KEY
  wave_of              TEXT NULL REFERENCES broadcast_rounds(broadcast_round_id)
                       CHECK (wave_of IS NULL OR wave_of <> broadcast_round_id)
  prompt_digest        TEXT NOT NULL          -- sha256 of the fan-out prompt
  requested_targets    INTEGER NOT NULL CHECK (requested_targets >= 1)
  deadline_at          INTEGER NULL
  status               TEXT NOT NULL CHECK (status IN ('open','closed'))
  disposition          TEXT NULL CHECK (disposition IS NULL OR disposition IN
                         ('all_completed','partial','none_completed'))
  created_at           INTEGER NOT NULL
  closed_at            INTEGER NULL

broadcast_legs
  broadcast_round_id      TEXT NOT NULL REFERENCES broadcast_rounds(...)
  leg_target              TEXT NOT NULL       -- peer instance / profile target
  client_id               TEXT NOT NULL       -- half of the binding key; see 3.2.1
  client_leg_request_id   TEXT NOT NULL       -- other half
  command_id              TEXT NULL UNIQUE REFERENCES dispatch_requests(command_id)
  leg_state               TEXT NOT NULL CHECK (leg_state IN
                            ('admitting','pending','completed',
                             'failed','timed_out'))
  terminal_at             INTEGER NULL
  PRIMARY KEY (broadcast_round_id, leg_target)
  UNIQUE (client_id, client_leg_request_id)
  CHECK (leg_state = 'admitting' OR command_id IS NOT NULL)
```

> **Round-2 finding (cx).** Draft 2 wrote its state enumerations as SQL
> comments. **SQLite does not constrain a value because a comment lists
> it** — every column above now carries a real `CHECK`. Trivially true
> and trivially missed; worth the line.

The remaining `CHECK` is load-bearing: a leg cannot leave `admitting`
without a command to point at. Draft 2's second `CHECK` — the one tying
`completed` to a non-null `response_artifact_ref` — is gone along with
the columns it guarded, and Section 3.3 explains why it was never doing
the job it appeared to do.

#### 3.2.1 Crash-safe linkage between admission and the leg row

> **Round-1 finding (cx).** Admission mints the `CommandID` internally, so
> a leg row carrying a `NOT NULL` FK to it cannot be written first — and a
> crash between the two writes leaves an admitted-but-uncorrelated
> command. Verified: `admit_request()` returns the minted
> `command_id` inside its `dispatch_admission` tuple
> (`direct_ask.py:210-227`, `source_read`); the caller learns it only
> after admission has committed.

> **Round-2 finding (cx), verified against the schema.** Draft 2's
> recovery scheme keyed on `client_leg_request_id` alone. **That is not
> the key peerhub enforces.** `client_request_bindings` has
> `PRIMARY KEY (client_id, client_request_id)`
> (`0003_command_request_attempt.sql:232`, `source_read`), and
> `command_idempotency_bindings` has
> `PRIMARY KEY (client_id, command_type, idempotency_key)` (`:245-249`).
> A single-column lookup would not be a lookup on the binding at all.

Resolved using machinery peerhub already has, rather than a new
mechanism:

1. The coordinator writes the leg row **first**, in state `admitting`,
   with `command_id NULL` and **both halves of the binding key**:
   a fixed `client_id` for the broadcast coordinator, and a deterministic
   domain-separated
   `client_leg_request_id = H("peerhub.broadcast.client-request.v1",
   broadcast_round_id, canonical_leg_target)`. The leg table carries
   `UNIQUE (client_id, client_leg_request_id)`, mirroring the binding PK
   exactly.
2. The coordinator independently derives the admission idempotency key as
   `H("peerhub.broadcast.admission-idempotency.v1",
   broadcast_round_id, canonical_leg_target)`. It supplies that value as
   `CommandEnvelope.idempotency_key` **and** binds the same value into
   `envelope.params["broadcast_leg_idempotency_key"]`. Distinct legs
   therefore occupy distinct command-idempotency namespaces, while an
   accidental key reuse across legs presents a different payload digest
   and fails closed instead of aliasing an existing command.
3. The binding pair and leg-scoped idempotency key are passed to
   `admit_request()`. Admission's existing
   `client_request_bindings` row then maps it to the minted command —
   that is precisely what the table is for.
4. The leg is updated to `pending` with the returned `command_id`.

A crash anywhere in that sequence is recoverable by a join on
`(client_id, client_request_id)`: a leg stuck in `admitting` either has a
binding row (adopt the existing command) or does not (re-admit under the
same deterministic pair, idempotently). No orphan is possible and no new
durable identity is introduced.

##### The payload digest does not cover the prompt

> **Round-2 finding (cx), verified.** `canonical_payload_digest`
> (`dispatch/model.py:69-95`, `source_read`) hashes exactly:
> `protocol_major`, `schema_version`, `authenticated_principal`, `scope`,
> `method`, `params`, `expected_revisions`, `completion_contract`.
> **The prompt is not in `CommandEnvelope` at all** — `direct_ask.py`
> puts it in `AdapterRequest.prompt_content` (`:245`), constructed after
> admission, and its `params` carries only
> `required_capability_tier` (`:189-193`).

The consequence is sharper than "a digest can't resume a leg." Because
admission compares digests to detect a *conflicting* resubmission
(`dispatch/admission.py:158`), and the digest is prompt-blind, a
resubmission carrying a **completely different prompt** under the same
`(client_id, client_request_id)` would present an identical digest and be
accepted as the same request. For a broadcast that is not hypothetical:
every leg of every round shares `scope`, `method`, `params`, and
completion contract, so all of them collide by default.

Fix, using the existing projection rather than a parallel one:
**the broadcast envelope binds `broadcast_round_id`, `prompt_digest`,
and the derived `broadcast_leg_idempotency_key` into
`envelope.params`**, which *is* inside the hashed projection. The digest
then distinguishes rounds, prompts, and legs; a resubmission whose prompt
differs or a different leg that accidentally reuses an idempotency key
fails the digest comparison admission already performs.

Recovery then has two admissible shapes, and the design requires one of
them explicitly rather than leaving it to the implementer:

- **Resubmission-with-matching-digest** (preferred): recovery replays the
  same prompt from the caller; a mismatch is rejected by admission's
  existing check. Requires the caller to still hold the prompt.
- **Prompt retention**: the round retains the prompt text for the
  recovery window.

Preferred is resubmission, because prompt retention re-introduces exactly
the content-durability question Section 3.3 defers, and would smuggle it
back in through a side door. A round whose caller is gone cannot be
resumed; it is closed as `none_completed`/`partial` on deadline. That is
a stated limitation, not an oversight.

#### 3.2.2 `wave_of` cycle safety — draft 2's argument was wrong

Draft 1 asserted "no cycle risk given rounds are append-only." Round 1
correctly called that intent rather than enforcement. Draft 2 replaced it
with a "DAG by construction" argument from FK + immutability.

> **Round-2 finding (cx) — a proven bug, not a stylistic objection.**
> SQLite defers FK checking to end-of-statement, so a **single multi-row
> INSERT** containing both `A -> B` and `B -> A` satisfies both FKs
> (each parent exists by the time checking runs) and creates a 2-cycle.
> Draft 2's argument assumed one row per statement and silently failed
> otherwise.

I reproduced this rather than taking it on report (`empirical_probe`):

```
-- single multi-row INSERT with A->B and B->A --
  RESULT: ACCEPTED  <-- cycle created, CHECK+FK did not stop it
  rows: [('A', 'B'), ('B', 'A')]
  fk violations: []
-- two separate single-row INSERTs --
  A->B (B absent): rejected IntegrityError: FOREIGN KEY constraint failed
```

So the invariant held only under an unstated assumption. Draft 2 claimed
schema enforcement it did not have — the same failure mode as draft 1,
one level deeper.

**Fix: a `BEFORE INSERT` trigger requiring the parent to already exist**,
which is stricter than the FK because it runs per row *before* that row
is visible:

```sql
CREATE TRIGGER broadcast_rounds_wave_parent_must_preexist
BEFORE INSERT ON broadcast_rounds
WHEN NEW.wave_of IS NOT NULL
 AND NOT EXISTS (SELECT 1 FROM broadcast_rounds
                 WHERE broadcast_round_id = NEW.wave_of)
BEGIN
  SELECT RAISE(ABORT, 'wave_of parent must already exist');
END;

CREATE TRIGGER broadcast_rounds_reject_existing_id
BEFORE INSERT ON broadcast_rounds
WHEN EXISTS (SELECT 1 FROM broadcast_rounds
             WHERE broadcast_round_id = NEW.broadcast_round_id)
BEGIN
  SELECT RAISE(ABORT, 'broadcast_round_id already exists');
END;

CREATE TRIGGER broadcast_rounds_wave_immutable
BEFORE UPDATE OF wave_of ON broadcast_rounds
WHEN NEW.wave_of IS NOT OLD.wave_of
BEGIN
  SELECT RAISE(ABORT, 'wave_of is immutable after insert');
END;
```

The first seven cases below were the draft-3 probe. Migration `0020`
adds and measures the two conflict-rewrite cases that round 3 exposed:

| Case | Result |
| --- | --- |
| multi-row `A->B, B->A` (the cx cycle) | rejected |
| multi-row `B->A` then `A` (reverse order) | rejected |
| self-reference `A->A` | rejected |
| root then wave 2, separate statements | accepted |
| legitimate 3-wave chain | accepted |
| multi-row, parent first, same statement | accepted |
| cycle attempted via `UPDATE` after insert | rejected |
| cycle attempted via `INSERT OR REPLACE` of an existing root | rejected |
| cycle attempted via `ON CONFLICT DO UPDATE` | rejected |

With the parent required to pre-exist at insert and `wave_of` immutable
thereafter, every edge points strictly backwards in *insertion* order —
now actually true, because no row can reference a row inserted in the
same statement or later. Legitimate parent-first bulk inserts still work,
so the fix costs nothing in expressiveness. The reject-existing-ID
trigger is separately load-bearing: SQLite implements `OR REPLACE` as a
delete-and-insert conflict action, so update immutability alone cannot
guard that path.

Required tests, named in Section 6: all seven rows above.

`wave_of` is what makes tonight's two-wave pattern first-class: wave 2
(cross-critique of a synthesis) records that it descends from wave 1, so
the deliberation is reconstructable as a tree rather than as two
unrelated rounds.

### 3.3 Response durability — descoped from Primitive A, deferred to its own increment

Draft 1 argued for durable correlation on the grounds that "a crash after
two of three legs returned discards both." Round 1 showed a correlation
row does not fix that, because `DecodedOutput.canonical_text`
(`adapters/contract.py:561`) is persisted nowhere — `AskResult`
(`dispatch/contract.py:351`) is four metadata fields, and
`dispatch_attempts.result_json` (`sqlite_dispatch.py:678`) stores those.
Draft 2 therefore proposed a response artifact with digest and retention.

Round 2 found three integrity gaps in that branch, and recommended
in-memory-first with durable transcripts deferred. **I evaluated the
recommendation and I am adopting it — but the cost argument is not my
main reason.**

#### 3.3.1 The real reason: draft 2 violated this document's own core principle

Section 3.1's whole thesis is that broadcast is **N ordinary dispatches
plus a coordinator**, inheriting the dispatch path rather than forking
it. Draft 2 then proposed that broadcast persist response transcripts —
something the single-peer dispatch path does not do. `direct_ask` reads
`canonical_text` once at `:277`, returns it to the caller, and drops it.

That would have made broadcast the **only** place in peerhub where
response content is durable, built on a primitive that has no such
guarantee. Broadcast would have had a stronger durability property than
the thing it wraps. That is the layering inverted, and it is the same
mistake in kind that Section 3.1 exists to prevent.

Response-transcript durability is a property of **dispatch**, not of
**broadcast**. If peerhub wants durable transcripts — and it may well —
that belongs in an increment on the dispatch path, where `direct_ask`
benefits equally and where the artifact-lifecycle questions get answered
once. Broadcast then inherits it for free, exactly as it inherits the
capability-lease gate.

So the descope is not a retreat under cost pressure. It restores the
design's own principle, which draft 2 broke while trying to patch a hole
round 1 opened.

#### 3.3.2 What Primitive A therefore does and does not guarantee

**Durable (correlation):** which legs exist, their target, their
`(client_id, client_leg_request_id)` binding pair, their `command_id`,
their terminal state, and the `wave_of` tree.

**In-memory (content):** peer response text, matching `hub.py`'s
`ask-all` (`hub.py:7581-7583`) and matching peerhub's own single-peer
path today.

The concrete value of durable correlation, absent durable content, is
narrower than draft 2 implied and worth stating plainly:

- **Recovery does not re-dispatch a leg that already ran.** A completed
  leg's text is lost on crash, but the record proves it ran, so recovery
  does not spend quota and wall-clock re-asking a peer that already
  answered. Given the three `ag` crashes tonight, this is the operational
  win that survives the descope.
- **Audit and the deliberation tree** survive: which peers were asked
  what round, in what wave, with what outcome.
- **What is lost on crash:** the response text. Stated, not hidden.

If a ratification round decides even correlation is over-built for an
unexercised pattern, dropping to fully in-memory is a coherent smaller
step. I do not recommend it — the no-re-dispatch property is cheap and
directly addresses a failure this session hit repeatedly — but the
position is defensible and the schema is small enough to add later.

#### 3.3.3 Deferred increment: durable response transcripts

Same posture as the Alembic hold and Primitive B: designed enough to be
picked up, explicitly unbuilt, with its known problems recorded so the
next session does not rediscover them.

**Trigger:** when a consumer actually needs post-hoc response retrieval.
The two candidates are `ConsensusRound.deliberation_ref` (Section 4.4),
which is itself deferred, and any shadow-validation comparison in Phase 4
that needs to diff peerhub and hub.py responses after the fact. Neither
exists today.

**Known open problems** — all three found by cx in round 2, all verified
here, none solved:

1. **The artifact subsystem is input-oriented and cannot simply be
   reused.** `ArtifactState` runs `DECLARED → STAGED → VERIFIED →
   RESERVED → CONSUMED → ORPHANED → CLEANED`
   (`dispatch/contract.py:1140-1149`, `source_read`) — a lifecycle for
   content supplied *before* invocation and consumed *by* it. The
   materializer's own contract states "manifest_digest is derived inside
   `materialize()` from durable immutable facts only — never accepted
   from a caller" (`dispatch/materializer.py:12-14`), and materialization
   runs pre-spawn. Capturing output inverts the core invariant: the
   expected digest cannot be known in advance. There is no state meaning
   "produced by the process, captured after exit." Reuse requires either
   extending the lifecycle or building a sibling output-artifact path —
   an open design question, not a wiring task.
2. **The terminalize-then-spill crash window.**
   `dispatch_and_execute()` commits the attempt's terminal result before
   returning; the coordinator would spill `canonical_text` after. A crash
   in between leaves the attempt durably terminal with no response, and
   the two records disagree. Draft 2's `CHECK` did not help — it would
   merely have left the leg in a non-terminal state while the attempt was
   terminal. The three options are spill-before-terminal-commit,
   streaming spill during execution, or an explicitly-modelled
   accepted-loss outcome. **Not chosen here**, because the choice belongs
   to whoever designs dispatch-layer durability, and picking one now
   would prejudge it.
3. **A reference column does not prove retrievability.** Draft 2's
   `CHECK (response_artifact_ref IS NOT NULL)` proved a non-null string
   and nothing else. A real design needs a foreign key to a genuine
   artifact identity with custody semantics, plus — per cx — a
   `response_state` (`available | expired | spill_failed`) **orthogonal
   to** `leg_state`, rather than draft 2's
   `completed_response_unrecoverable`, which overloaded dispatch outcome
   with retention outcome. Draft 2 conflated "the peer answered" with
   "we can still read the answer"; those are independent facts and need
   independent fields.

**Retention policy** remains required for that increment, and its window
default remains `TEST NEEDED` — no measurement of realistic transcript
sizes exists in this environment and none is estimated here.

### 3.4 Failure and timeout semantics

- **Per-leg timeouts are independent.** A slow leg does not extend the
  others.
- **A round closes when every leg is terminal, or when `deadline_at`
  passes** — whichever comes first. Legs still pending at deadline become
  `timed_out`.
- **Partial success is a first-class outcome, not an error.** Tonight's
  work proceeded on cc+cx while ag was repeatedly unavailable. hub.py
  already behaves this way (`action_ask_all` exits 0 if any peer exited
  0, `hub.py:7628-7640`); peerhub should make it explicit in
  `disposition` rather than implicit in an exit code.
- **`none_completed` is reported, not raised.** The caller decides
  whether zero responses is fatal for its purpose.
- **No sweep daemon.** Section 1.4's measurement — timeout is the single
  largest abnormal outcome in hub.py's history — argues against a design
  where rounds linger waiting for an external sweeper. A round with a
  deadline closes itself on next inspection; there is no separate
  scheduled process that must run for state to be correct.

### 3.5 Parallelism is an optimization, not a correctness requirement

hub.py fans out with one `threading.Thread` per peer, each running a
`subprocess.run` of `hub.py ask` (`hub.py:7585-7613`). peerhub's dispatch
path is synchronous and process-supervised.

**A sequential fan-out is fully correct** — it is only slower. This
matters: it means the broadcast primitive can land, be tested, and be
ratified without first resolving peerhub's concurrency model, and
parallelism becomes a later, independently-reviewable change.

Two concerns to settle before parallelising, neither resolved here:

- **SQLite write contention.** N concurrent legs each open multiple write
  transactions against one database. peerhub uses WAL, `BEGIN IMMEDIATE`,
  and `busy_timeout = 5000ms` (`persistence/sqlite.py`). Whether a
  realistic fan-out (N=3) contends measurably, and where the ceiling is,
  is `TEST NEEDED` — not estimated here.
- **Process supervision under concurrency.** The heartbeat/timeout
  machinery in `dispatch/heartbeat.py` and `dispatch/process.py` has been
  exercised for one supervised process at a time. `TEST NEEDED`.

---

## Section 4 — Primitive B: `ConsensusRound` (designed, NOT built now)

Section 2.2 establishes that R:10 cannot be expressed without this.
Section 5 explains why it should not be built yet. This section specifies
it anyway, so that a future increment starts from a ratifiable design and
so Primitive A can be checked for foreclosure (it does not foreclose it).

> **Drift note (2026-08-12 P:↔peerhub gap analysis).** This section's
> hub.py citations (`hub.py:7711-7718` RED-voter filtering,
> `hub.py:7748` mid-round gate-closure escalation) describe hub.py's
> consensus behavior as of this document's original drafting. P:'s own
> hub.py has since changed both: commit `b56da31` fixed an INV-03
> violation so RED voters are no longer silently dropped from the
> initial snapshot, and commit `f8467b3` introduced a frozen
> `quorum_snapshot` captured at round start, with `_decide_consensus`
> rewritten to rely exclusively on that snapshot -- **the mid-round live
> health re-evaluation this section cites at `hub.py:7748` was removed
> entirely**, not just changed. Section 4.1's own `consensus_round_voters`
> table already independently arrives at a frozen-snapshot design (rows,
> not a re-read), so the *intent* here already matches hub.py's current
> behavior better than this section's stale citations suggest -- but
> whoever picks up this increment must re-verify against hub.py's
> *current* source before implementing, not against the line numbers
> cited here.

### 4.1 Proposed durable state

```
consensus_rounds
  consensus_round_id   TEXT PRIMARY KEY
  subject              TEXT NOT NULL
  subject_digest       TEXT NOT NULL          -- sha256; MAX_ROUNDS keys on this
  proposed_by          TEXT NOT NULL
  collab_rate_snapshot INTEGER NOT NULL
  decision_rule        TEXT NOT NULL          -- unanimous | majority
  status               TEXT NOT NULL          -- voting | finalized | rejected | escalated
  outcome              TEXT NULL
  proposed_at          INTEGER NOT NULL
  deadline_at          INTEGER NOT NULL
  closed_at            INTEGER NULL

consensus_round_voters                        -- THE quorum snapshot
  consensus_round_id   TEXT NOT NULL REFERENCES consensus_rounds(...)
  voter_id             TEXT NOT NULL
  eligible             INTEGER NOT NULL       -- 0/1, frozen at propose time
  health_status        TEXT NOT NULL          -- observed status at propose time
  excluded_reason      TEXT NULL
  PRIMARY KEY (consensus_round_id, voter_id)

consensus_votes
  consensus_round_id   TEXT NOT NULL REFERENCES consensus_rounds(...)
  voter_id             TEXT NOT NULL
  vote                 TEXT NOT NULL CHECK (vote IN ('agree','disagree','abstain'))
  reason               TEXT NOT NULL
  deliberation_ref     TEXT NULL REFERENCES broadcast_legs(command_id)
  cast_at              INTEGER NOT NULL
  PRIMARY KEY (consensus_round_id, voter_id)
  FOREIGN KEY (consensus_round_id, voter_id)
      REFERENCES consensus_round_voters(consensus_round_id, voter_id)
```

Three structural improvements over hub.py's JSON blob, each replacing an
imperative check with a constraint:

- **The quorum snapshot is rows, not a nested dict.** `_decide_consensus`
  reads only `consensus_round_voters`; there is no code path that *could*
  re-read live health, so hub.py's "Live peer health is NEVER re-read"
  comment (`hub.py:7754`) becomes a schema property instead of a
  convention.
- **Vote immutability is the primary key.** hub.py enforces it with an
  explicit `VOTE_ALREADY_CAST` branch in two places (`hub.py:7894`,
  `7826`). Here a second insert simply fails.
- **A vote can only come from a snapshotted voter** — the composite FK.
  hub.py checks `voter not in data["voters"]` imperatively, twice.

### 4.2 Decision function

One pure function over `(round row, voter rows, vote rows)`, called inside
the same write transaction as the vote insert. It reproduces
`_decide_consensus`'s ratified semantics (`hub.py:7748-7793`) — including
the INV-03 fix and the fail-closed treatment of a snapshot-less round —
but reads only frozen rows. No live health, no configuration re-read.

Ordering of the existing rules is preserved exactly: any `disagree` →
`rejected`; fewer than two required voters, or no non-proposer `agree` →
`escalated/human_gate`; all agree → `finalized/unanimous`; otherwise at
`collab_rate >= 10` → `escalated/human_gate_unanimity_failed`; else
`finalized/majority`.

### 4.3 Failure and timeout

`deadline_at` is set at propose time from
`protocol.json.consensus.timeout_minutes` (currently 30, `source_read`).
As with broadcast, an expired round closes itself as
`escalated/timeout` on next inspection rather than requiring a sweeper to
have run. Given Section 1.4 — timeout is hub.py's single largest abnormal
outcome — a self-closing round is a deliberate correction, not a port.

`MAX_ROUNDS` keys on `subject_digest`, counting prior `rejected` rounds,
replacing hub.py's glob-and-string-match.

### 4.4 `deliberation_ref` — the one genuinely new idea here

A vote may cite the broadcast leg that produced the voter's position.

> **Consequence of draft 3's descope (Section 3.3).** With response
> transcripts deferred, a `deliberation_ref` points at a leg whose text
> is *not* retrievable. It proves a dispatch to that peer occurred, in
> that round, with that outcome — it does not let a reader check what the
> peer actually said. That is weaker than draft 1 and draft 2 implied,
> and it is a real reduction in the idea's value.

This tightens the dependency chain rather than breaking it: `Primitive B`
already depends on the deferred transcript increment
(Section 3.3.3) for `deliberation_ref` to mean what it claims. Both are
unbuilt, and the transcript increment should land **before or with** B,
not after — otherwise B ships an evidence field that cannot be audited.
Recorded in Section 5.1's readiness list.

Proof-of-dispatch alone is still strictly more than hub.py has (a free-text
`reason` and nothing else), so the idea survives the descope in reduced
form. It just stops being an evidence mechanism and becomes a provenance
mechanism.

This is the capability-lease evidence discipline applied to consensus: in
hub.py a vote's `reason` is free text with nothing behind it (measured:
median 66 characters, `empirical_probe`), so a round can be *fabricated*
by one actor writing three plausible strings — which Section 1.3's
timestamp data suggests is close to what routinely happens.

With `deliberation_ref`, a vote either points at a recorded dispatch that
actually occurred, or it is `NULL`. Rounds where every vote is `NULL` are
reported as **recorded, not deliberated** — not rejected, and not dressed
up as something stronger. That mirrors increment 5's finding, where the
honest move was to leave `"unverified"` tags in place rather than
fabricate evidence.

I want to flag this as the part of the design most likely to be wrong:
it adds friction to a mechanism that is already being bypassed (zero
rounds in August). A reasonable counter-position is that making
ratification *harder* is exactly backwards. I hold it as proposed, not
settled.

---

## Section 5 — Recommendation, and what is explicitly not being built

### 5.1 Recommendation

**Build Primitive A (`BroadcastDispatch`), correlation-durable and
content-in-memory. Defer durable response transcripts (Section 3.3.3) and
Primitive B (`ConsensusRound`) to their own separately-ratified
increments.**

Draft 3 narrows the build from draft 2: the initial increment is now one
coordinator, two small tables, and a sequential fan-out — with three
things explicitly held back, each with a named trigger:

| Deferred | Trigger | Section |
| --- | --- | --- |
| Durable response transcripts | a consumer needs post-hoc retrieval | 3.3.3 |
| `ConsensusRound` | before R:10 traffic routes to peerhub | 5.1 below |
| Parallel fan-out | after contention is measured | 3.5 |

Ordering constraint added in draft 3: **the transcript increment must
land before or with Primitive B**, because `deliberation_ref` (4.4) is a
provenance marker rather than an evidence mechanism until it does.

Reasoning, in order of weight:

1. **Measured usage supports A and does not support B.** Section 1: the
   two-wave fan-out was exercised repeatedly tonight; zero elements of the
   voting subsystem were.
2. **peerhub is not the enforcement point for DIR-006 today.** hub.py is.
   Building peerhub-native voting while hub.py owns the protocol produces
   two consensus systems that can disagree about the same decision — a
   strictly worse position than one.
3. **Even hub.py's version is being bypassed.** No round since
   2026-07-28. Porting a mechanism whose adoption is currently zero,
   before anything routes through peerhub, is building for an unexercised
   pattern.
4. **A does not foreclose B.** `broadcast_legs.command_id` is exactly the
   referent `consensus_votes.deliberation_ref` needs. No schema change to
   A is required to add B later.

Trigger for B — **tightened in round 1 (cx)**. Draft 1 said "build it
when peerhub becomes the primary dispatch path for an R:10-gated decision
class." cx is right that this is the wrong side of the line: it would
start B *after* R:10 traffic had already begun flowing through a system
with no way to finalize a round, i.e. a window in which peerhub carries
governed decisions it cannot mechanically ratify.

Corrected: **Primitive B is a readiness prerequisite for that cutover,
not a follow-up to it.** B must be implemented, tested, and ratified
*before* the first `r10_requires_finalized_for` decision class is routed
to peerhub. Concretely, B enters the Phase 4 shadow-validation gate as a
blocking item rather than sitting in a Phase 3 backlog.

This does not change the "not now" conclusion — nothing routes through
peerhub today and the trigger has not fired. It changes *what counts as
having fired*: the trigger is the decision to route R:10 traffic, not the
arrival of it. Same posture as the Alembic increment-2 hold, with a
correctly-placed boundary.

### 5.2 Explicitly not being built, and why

- **`ConsensusRound` / voting** — Section 5.1. Designed in Section 4,
  not implemented.
- **Room/thread messaging** (`thread-new`, `thread-append`,
  `thread-react`, `thread-promote`, `new-topic`). Hub-ecosystem UX with
  no peerhub consumer. No peerhub usage has ever exercised these, and
  nothing in R:6-10 requires them.
- **`ask-coordinator` and thin-envelope forwarding**
  (`_thin_forward_envelope`, `hub.py:7540`). Leader election is outside
  peerhub's scope, and `protocol.json` pins the human-interface peer to
  `cc` with `human_interface_peer_stays_fixed: true` regardless
  (`source_read`).
- **The DIR-005 arbiter auto-wire.** Separate ratified mechanism,
  terminal-applied. Not a consensus primitive.
- **DocsSyncer capsule emission** (`_emit_decision_capsule`,
  `hub.py:7923`). Hub ecosystem integration — peerhub has no DocsSyncer
  and should not grow one.
  **Scope correction (cx, round 1):** draft 1 wrote this exclusion as
  "decision capsules," which over-excluded. A *domain-level immutable
  decision receipt* — a durable, digest-bound record that round X closed
  with outcome Y over voter set Z — is a different thing from emitting a
  file for an external doc pipeline, and it is arguably the whole point
  of having `ConsensusRound` at all. It is **not** excluded here. It is
  deferred to B's detailed design, where the open question is whether the
  `consensus_rounds` row *is* the receipt or whether a separate
  append-only receipt with its own digest is warranted. Recorded now so
  the exclusion list does not silently foreclose it.
- **The sandbox broker vote-merge path.** Section 2.3 — solves a problem
  peerhub does not have.
- **Any live-health re-read during a round.** Prohibited by protocol.md
  §4.4's round-scoped gate rule and by construction in Section 4.1.
- **Parallel fan-out, in the first increment.** Section 3.5 — correctness
  does not depend on it.
- **Durable response transcripts** (added draft 3). Section 3.3 — a
  dispatch-layer property, not a broadcast one; deferred to its own
  increment with three known open problems recorded in 3.3.3. Primitive A
  keeps response text in memory, exactly as the single-peer path does
  today.

### 5.3 Question status — both Primitive-A gates are CLOSED

> **Updated 2026-08-11 (Stage 0 accuracy pass).** This section previously
> listed two questions as "gating Primitive A." **Both are resolved.** An
> implementer reading the old text would have wrongly concluded design
> work remained before `fan_out()` could be built. It does not.

**Resolved — no longer gating:**

1. ~~Is correlation-durable the right floor, or is fully in-memory
   enough?~~ **RESOLVED: correlation-durable.** Migration
   `0020_broadcast_correlation` landed (commit `8650314`) storing
   correlation only — no response text, digest, or artifact column. The
   deciding argument is in Section 3.3.1: response-transcript durability
   is a dispatch-layer property, and giving broadcast a stronger
   guarantee than the path it wraps inverts the layering Section 3.1
   protects. The retention-window sub-question moved to the deferred
   transcript increment (3.3.3).
2. ~~Recovery shape: resubmission-with-matching-digest, or prompt
   retention?~~ **RESOLVED: resubmission-with-matching-digest.** Round 3
   (Section 7.3, finding 2) bound the leg idempotency key —
   domain-separated hashes of `(broadcast_round_id,
   canonical_leg_target)` — into `envelope.params`, which is inside the
   hashed payload projection. A replay under changed prompt content now
   fails by digest, proven by
   `test_broadcast_leg_prompt_digest_conflict_is_rejected`. Prompt
   retention was rejected because it smuggles content durability back in
   through a side door.

**Still open, none blocking Primitive A's implementation:**

3. **SQLite write contention under concurrent legs** (Section 3.5).
   `TEST NEEDED` before any *parallel* implementation — not before the
   sequential one. The first increment is sequential by design.
4. **Should peerhub's broadcast read hub.py's peer health, or only its
   own?** Section 2.3 argues only its own, accepting that the two systems
   can disagree about eligibility during the transition window. Should be
   voted on rather than assumed, but does not block `fan_out()`.
5. **Does `ConsensusRound` need a separate immutable decision receipt?**
   (Section 5.2). Deferred to Primitive B's detailed design.

**Net: `BroadcastCoordinator.fan_out()` is ready to implement against
Section 6 with no unresolved design question.** What is unbuilt is code,
not decisions.

---

## Section 6 — Implementation sequence

> **Updated 2026-08-11 (Stage 0 accuracy pass).** This section opened
> "Not a commitment; a scoping sketch so the round has something concrete
> to vote on." That was accurate for draft 3, and it is why the roadmap's
> earlier claim that Section 6 was "a ready-to-execute spec" was
> challenged in review. With round 3 closed and step 2 landed as
> migration `0020`, the sketch has become the sequence: **steps 1-2 are
> done, steps 3-6 are the remaining work**, and Section 5.3 records that
> no design question gates them.

Draft 3's descope removed draft 2's response-durability step entirely and
shrank step 3.

1. **Contracts only** — `BroadcastRound`/`BroadcastLeg` frozen DTOs plus
   validation, no call sites. Mirrors capability-lease increment 1.
2. **Migration `0020`** — the two tables from Section 3.2, including
   every `CHECK (… IN (…))` enumeration, the
   `wave_of` parent-must-preexist trigger, and the `wave_of` immutability
   trigger, plus the reject-existing-round `BEFORE INSERT` trigger that
   closes `INSERT OR REPLACE` and UPSERT conflict rewrites; plus
   read/write UoW repository methods and
   rollback/replay-identity tests. Authored as a bespoke `.sql` per the
   ratified Alembic hold; note the runner now derives the sequence from
   disk, so no code registration
   step exists.
3. **`BroadcastCoordinator.fan_out()`** — sequential.

   > **Round-1 correction (cx).** Draft 1 described this as "N ordinary
   > `dispatch_and_execute` calls." That is imprecise:
   > `dispatch_and_execute` requires an already-admitted and prepared
   > command plus a `capability_lease_id`; it performs no admission
   > itself (`workflows.py:540-579`, `source_read`).

   The full per-leg pipeline, mirroring `direct_ask.py:210-272`:

   a. Write the leg row in state `admitting` with **both** binding-key
      halves, `client_id` and
      `client_leg_request_id = H(broadcast_round_id, leg_target)`
      (Section 3.2.1).
   b. `admit_request(...)` with `envelope.params` carrying
      `broadcast_round_id` and `prompt_digest`, so the payload digest is
      round- and prompt-sensitive (Section 3.2.1). Yields the minted
      `command_id`, the `capability_lease_id`, and the route.
   c. Update the leg to `pending` with the returned `command_id`.
   d. `prepare_for_dispatch(command_id, route_decision_id=..., ...)`.
   e. Build the per-leg `AdapterRequest` with
      `prompt_content=<round prompt>`, `prompt_reference=None`.
   f. `dispatch_and_execute(...)`; hand `decoded_output.canonical_text`
      to the caller in memory.
   g. Set the terminal leg state.

4. **Two-wave support** — `wave_of` threading plus a test reproducing
   tonight's exact pattern: three independent positions, then a
   cross-critique wave over a synthesis. Plus **all seven** cycle-safety
   cases tabulated in Section 3.2.2 — in particular the multi-row
   `A->B, B->A` insert, which is the case draft 2 silently permitted.
5. **Crash-linkage tests** — kill between steps (a) and (b), and between
   (b) and (c); assert recovery adopts the existing command by joining
   `client_request_bindings` on `(client_id, client_request_id)` rather
   than double-admitting, and that no leg is left orphaned. Plus a
   negative test: resubmission of a leg under the same binding pair with
   a **different prompt** must be rejected by admission's existing
   digest comparison — the hole that exists today because the digest is
   prompt-blind (Section 3.2.1). Also force two different targets to reuse
   one command idempotency key and prove the leg-bound parameter digest
   rejects the alias.
6. **Partial-failure tests** — one leg fails, one times out, one
   succeeds; assert `disposition = partial` and that the successful leg's
   correlation row survives a simulated coordinator crash, and that
   recovery does **not** re-dispatch it.

Two acceptance criteria that pin properties rather than behaviours:

- Every leg's `AdapterRequest` carries `prompt_reference=None`
  (Section 3.1.1), so the no-shared-file property is pinned rather than
  assumed. If a future adapter begins honoring `prompt_reference`, this
  test fails and forces the coordinator to own per-leg reference
  generation.
- No table introduced here stores response content (Section 3.3). If a
  later increment adds transcript durability it should do so on the
  dispatch path, and this test is the tripwire that makes a
  broadcast-local shortcut visible.

Acceptance discipline as with the capability lease: security-relevant
behavior proved by mutation-tested tests, not by assertion.

---

## Section 7 — Review record

### 7.1 Round 1 (draft 1 → draft 2)

Reviewers: `ag.deepthink` (full endorsement, no findings),
`cx.deepthink` (endorsed the Section 1 measurements — independently
reproduced the corpus numbers exactly — and the A/B split, with seven
findings against the implementation reasoning).

Two findings were **independently re-verified against source by the
terminal and again by me** before acceptance; both were correct and both
invalidated a stated mechanism while leaving the conclusion intact:

| # | Finding | Disposition |
| --- | --- | --- |
| 1 | IPC-file-reuse closure mechanism false — adapters inline the prompt in `argv`, `artifacts=()` | **Fixed**, Section 3.1.1. Mechanism replaced; conclusion holds for a different reason |
| 2 | Durability argument does not cover response text — `canonical_text` persisted nowhere | **Fixed**, Sections 3.2/3.3. Schema extended; recommendation re-priced and weakened |
| 3 | Crash-safe linkage gap between admission and leg FK | **Fixed**, Section 3.2.1, using existing idempotency-binding machinery |
| 4 | `wave_of` cycle safety asserted, not enforced | **Fixed**, Section 3.2.2, with named tests |
| 5 | Section 6 "N ordinary `dispatch_and_execute` calls" imprecise | **Fixed**, Section 6 step 3, full 7-step pipeline |
| 6 | Primitive B trigger on the wrong side of the cutover | **Fixed**, Section 5.1, now a readiness prerequisite |
| 7 | Exclusion list over-excludes domain-level decision receipts | **Fixed**, Section 5.2, un-excluded and deferred to B's design |

Nothing was dropped silently. The most important consequence is not any
individual fix: it is that finding 2 **changed the recommendation's
cost**, moving durable-vs-in-memory from a footnote to open question 1.
Draft 1's "nothing else in the design depends on this choice" was the
single worst sentence in it.

### 7.2 Round 2 (draft 2 → draft 3)

Reviewer: `cx.deepthink`. Direction (the A/B split) approved again; the
IPC fix from round 1 confirmed clean; draft 2 judged not ready on the
durable-response branch.

| # | Finding | Disposition |
| --- | --- | --- |
| 1 | Section 1.5 still said "materialized prompt", contradicting the corrected 3.1.1 | **Fixed**, Section 1.5 rewritten |
| 2 | Recommend descoping durable transcripts to a separate increment | **Adopted**, Section 3.3 — but on a different and stronger rationale; see below |
| 3a | Artifact subsystem is input-oriented, not reusable for output capture | **Verified and recorded** as deferred-increment open problem 1 (3.3.3) |
| 3b | Crash window between attempt terminalization and spill | **Verified and recorded** as open problem 2 (3.3.3); deliberately not resolved here |
| 3c | `response_artifact_ref` not custody-constrained; retention state overloaded onto `leg_state` | **Verified and recorded** as open problem 3 (3.3.3), including cx's orthogonal `response_state` proposal |
| 4 | `wave_of` cycle safety not schema-enforced — multi-row INSERT defeats FK+CHECK | **Fixed**, Section 3.2.2, with a `BEFORE INSERT` trigger measured across 7 cases |
| 5 | Binding key is `(client_id, client_request_id)`, not `client_request_id` alone; payload digest excludes the prompt | **Fixed**, Section 3.2.1, both halves |
| 6 | Comment-only state enumerations do not constrain values | **Fixed**, Section 3.2, real `CHECK (… IN (…))` on all three |

Findings 4 and 5 were **reproduced/verified by me before acceptance**,
not taken on report. Finding 4's probe output is quoted inline in 3.2.2
because draft 2 made a schema-enforcement claim twice without measuring
it, and a third unverified assertion would have been the wrong way to
close it.

**On finding 2, where I did not simply defer to cx.** cx's argument was
cost: the durable branch needs infrastructure that doesn't exist. That is
true but not, I think, decisive on its own — peerhub builds infrastructure
routinely. The decisive argument is that draft 2 **violated Section 3.1's
own core principle**: it gave broadcast a durability guarantee stronger
than the single-peer dispatch path it wraps, which inverts the layering
this document exists to protect. Response-transcript durability is a
dispatch-layer property; if peerhub wants it, `direct_ask` should get it
too, and broadcast should inherit it the same way it inherits the
capability-lease gate. Same conclusion as cx, load-bearing for a reason
that survives even if the cost estimate is wrong.

The net effect across two rounds: draft 1 proposed the most; draft 3
builds the least. Every reduction came from a verified defect in the
reasoning, not from scope fatigue.

### 7.3 Round 3 (draft 3 to prototype)

Reviewer: `cx.deepthink`. The direction remained acceptable, but the
correlation and admission design was blocked on two new, independently
actionable defects. This met the author's pre-committed threshold to stop
iterating only on paper and prototype migration `0020` plus the real
admission replay path.

| # | Finding | Disposition |
| --- | --- | --- |
| 1 | `INSERT OR REPLACE` can delete and reinsert an existing root, bypassing the `UPDATE OF wave_of` immutability trigger and creating a cycle | **Fixed and empirically tested.** Migration `0020` adds `broadcast_rounds_reject_existing_id`, a second `BEFORE INSERT` guard; both `OR REPLACE` and `ON CONFLICT DO UPDATE` cycle attempts are rejected |
| 2 | The idempotency-key namespace was unspecified, so legs with otherwise equal payloads could alias one command | **Fixed and empirically tested.** Client-request IDs and command idempotency keys use separate domain-separated hashes of `(broadcast_round_id, canonical_leg_target)`; the leg idempotency key is also bound into `envelope.params`, making accidental key reuse a digest mismatch |

The two fixes are deliberately independent. The first is a SQLite
conflict-action property; the second is an admission identity property.
Neither is left to coordinator convention after the prototype.

---

## Section 8 -- Prototype validation

Migration `peerhub/persistence/migrations/0020_broadcast_correlation.sql`
implements the correlation-only schema from Section 3.2. It contains no
response text, response digest, or response-artifact column. The bespoke
runner discovers it from the contiguous filename sequence and records
schema version 20.

The empirical harness is
`tests/integration/persistence/test_broadcast_correlation_schema.py`.
Focused execution produced **7 passed**. Its evidence is split by
property rather than hidden behind one aggregate assertion:

- `test_migration_0020_registers_broadcast_schema` proves migration
  registration, schema head 20, and all three round triggers.
- `test_wave_of_original_seven_case_matrix` reruns all seven draft-3
  cases, including legitimate parent-first bulk insertion.
- `test_insert_or_replace_cannot_rewrite_root_into_cycle` proves the
  round-3 replacement attack is rejected and leaves the original DAG
  unchanged.
- `test_upsert_cannot_rewrite_root_into_cycle` proves the corresponding
  UPSERT conflict path is also rejected.
- `test_distinct_broadcast_legs_admit_distinct_commands_and_replay`
  exercises `DispatchService.admit_request`, proves two targets receive
  distinct FK-eligible command IDs, persists both leg links, and proves
  exact replay returns the original command ID without adding a request.
- `test_broadcast_leg_prompt_digest_conflict_is_rejected` proves the same
  round and leg cannot be replayed under changed prompt content.
- `test_reused_leg_idempotency_key_conflicts_across_targets` deliberately
  forces a second target to reuse the first target's command idempotency
  key and proves the leg-bound parameter digest raises
  `IdempotencyPayloadMismatchError` rather than aliasing the command.

The prototype surfaced no additional schema-design defect. It did expose
one easy implementation omission before finalization: merely assigning
the derived value to `CommandEnvelope.idempotency_key` is insufficient;
the same leg-scoped value must be present in hashed `params` for an
accidental cross-leg key reuse to fail by digest. The final harness pins
that requirement with the last negative test above.

Migration `0020` does not retroactively change the already-generated
Alembic v19 baseline. Its parity test now explicitly applies bespoke
migrations 1 through 19, preserving the exact comparison the frozen
baseline claims to make; current-head persistence tests separately assert
`PRAGMA user_version = 20`.
