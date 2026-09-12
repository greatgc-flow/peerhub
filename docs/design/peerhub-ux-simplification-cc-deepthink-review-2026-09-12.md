# PeerHub UX/structure — third, independent review (cc.deepthink, 2026-09-12)

Status: read-and-propose only. No code changed. Not a vote on anything already ratified.
Evidence tags follow DIR-004: `[probe]` = I ran it, `[code]` = read at the cited line, `[decl]` = a doc says so, not verified.

Ordering note: Part 1 was written and saved **before** I opened
`peerhub-ux-simplification-proposal-A/B/RATIFIED-2026-09-12.md`. The working tree I reviewed already
contains commit `5d4b90a` ("implement Tier 1 & Tier 2 backlog"), so some of what I saw (e.g. `ask -t`
defaulting to `READ_ONLY`) is already post-ratification. I did not read that commit's diff or message body
before writing Part 1.

---

## Headline (added after Part 2; see there for why it's new)

**PeerHub has no ambient dispatch context, and that single missing piece is simultaneously its biggest
ergonomics problem and a governance-integrity hole.** A peer spawned by `peerhub ask` is told nothing
about who it is, which workspace or room it's in, or which dispatch it belongs to. So every
governance command makes the caller retype that context as required flags. Those flags are self-asserted
strings that nothing verifies. **Measured:** in a throwaway workspace, one process ran `consensus vote
--actor cc` and then `--actor cx`, and the round reached `quorum_reached, 2/2` (Part 2 §2.0). The typed
command boundary that was designed to carry `actor_id` (`ApplicationAPI`/`Client`) has no production
caller. You cannot fix this at the flag/README layer. It sits exactly on the seam the prior reviews scoped
out as "core, already converged." Recommended action: a separate, DIR-006-gated design round (D-CTX,
§2.4), plus withdrawing RATIFIED's "governance verbosity is appropriate" non-change. No shipped Tier 1/2
item needs reverting, but the shipped P1b notice needs a small fix (it prints "initialized" when
nothing was initialized).

---

## Part 1 — Independent findings (written before reading A/B/RATIFIED)

### F1. No ambient dispatch context → a required-flag wall *and* spoofable governance identity (HIGH)

What I measured:

- `cli.py` has **82** `required=True` arguments and reads **zero** environment variables `[code: grep]`.
  The whole package reads only three `PEERHUB_*` vars (`PEERHUB_CONFIG_HOME`, `PEERHUB_SYS_DIR`,
  `PEERHUB_ERA_WRITES_PRESENT`), none of them about identity, room, or workspace `[code: grep]`.
- Governance commands require the caller to *assert* who they are and where they are:
  `consensus propose --proposer`, `consensus vote --actor`, `consensus proposal-vote --voter`,
  `task create --creator`, `room create --creator`, `duty claim --instance-id --profile-id
  --owner-principal-id --authority-epoch`, `session open --workspace-scope-id --actor-principal-id
  --instance-id --profile-id --session-fingerprint` `[probe: --help]`.
- All three real adapters spawn the peer with an **empty** environment delta:
  `agy_adapter.py:229`, `claude_adapter.py:279`, `codex_adapter.py:485` → `environment_delta={}` `[code]`.
  A peer dispatched by `peerhub ask` therefore cannot learn its own identity, room, workspace, or dispatch
  id except from prose in the prompt. When it wants to vote or ack a Final Call, it has to retype that
  prose into flags. (Even legacy `hub.py` propagates *some* context into workers: `HUB_ORIGIN` and
  `HUB_PEER_TIER`, `P:\_sys\core\hub.py:6934,7749` `[code]`.)
- Identity is **self-asserted, not verified**. `consensus vote` → `service.cast_vote(parsed.round_id,
  actor_id=parsed.actor, ...)` (`cli.py:878`), and the only check is membership in the eligible list
  (`governance/consensus.py:519-520`) `[code]`. `proposal-vote --voter` works the same way (`cli.py:830-834`).
  Any process that can run `peerhub` can vote as any eligible peer.
- A real authentication mechanism *does* exist, but it's wired to the wrong layer.
  `LocalProcessCallerIdentityProvider` (`core/identity.py:47-66`) authenticates the OS process owner as
  `local-cli:<account>`. It's used at about six CLI sites (`ask`, peer recover/quarantine, a few others,
  `cli.py:182,207,302,716,1374,1396,1521`), and never for votes, proposals, acks, or claims. Even where it
  is used, it can't tell `cc` from `cx`, because all peers run under the same OS account.
- The typed command boundary (`ApplicationAPI` + `Client`, whose `CommandEnvelope` carries `actor_id`,
  `idempotency_key`, `expected_policy_revision`) is constructed in `runtime.py:371` but has **no
  production caller**. `Client(` is never instantiated in `peerhub/`, and every CLI handler calls
  services directly (e.g. `cli.py:797` → `ConsensusService(...)`) `[code: grep]`. It's referenced only from
  `tests/` (19 references across 13 files). This is the same shape as the LegacyTranslator finding from
  2026-09-07 (a tested-but-unreachable layer), just one layer down.

Why this is one finding, not three: the flag wall, the spoofable vote, and the orphaned envelope all have
one root cause. **The dispatch that created a peer never hands that peer a context it can present back.**
Cosmetic CLI fixes (short flags, better defaults) can't solve it. Some of them would make it worse:
defaulting `--actor` from `$USERNAME` or config would make impersonation *quieter*.

Proposal: a **dispatch context envelope**.

1. When peerhub spawns a peer, fill `environment_delta` with `PEERHUB_WORKSPACE` (absolute root),
   `PEERHUB_ROOM` (if any), `PEERHUB_DISPATCH_ID`, `PEERHUB_PEER` (the profile id it resolved), and
   `PEERHUB_DISPATCH_TOKEN`. The token is a random per-dispatch secret. Store only its hash on the dispatch
   row, bind it to that dispatch's lease lifetime and capability tier, and have it die when the lease ends.
2. The CLI resolves defaults in this order: explicit flag > envelope > (for humans) nothing. `--workspace`,
   `--room-id`, `--actor/--proposer/--creator/--voter` stop being *required* whenever an envelope is
   present.
3. Governance writes record **provenance** on every vote, ack, claim, and proposal: `dispatch-token`
   (verified: the token's bound peer == actor), `local-cli` (a human at a terminal, OS-authenticated), or
   `asserted` (a flag with no evidence). An explicit `--actor` that contradicts a present token is rejected.
4. Quorum policy can then require `dispatch-token` or `local-cli` provenance for high-risk rounds (the
   DIR-005 trigger class) without touching anything else.
5. Route these writes through `ApplicationAPI.submit` so `actor_id`/idempotency go through the boundary
   that was built for them. Otherwise, delete the envelope layer honestly, the way the LegacyTranslator
   was deleted.

Limits, stated honestly (DIR-004): an env-var token is readable by any same-user process, so this defends
against *confusion and accidental impersonation*, not a hostile local attacker. Accidental impersonation is
the realistic failure in a system where one prompt can tell several peers "you are cx." **TEST NEEDED
before designing further:** does each peer CLI pass its parent environment through to the tool/shell
subprocesses the model actually runs? Codex's sandbox and shell-environment policy may scrub
unknown vars, Claude Code's Bash tool inherits by default `[decl, unverified]`, and agy PTY mode is unknown.
If any peer scrubs env, that peer needs a file-based fallback, e.g. a 0600 context file under
`.peerhub/run/<dispatch_id>.json` whose path is passed in the prompt.

### F2. Workspace resolution is a literal `.`, and `ask` silently forks state by cwd (HIGH)

- `PathLayout.for_workspace` is `workspace_root / ".peerhub"`, with no upward discovery
  (`core/context.py:40-48`) `[code]`. **61** `--workspace` registrations default to `"."` `[code: grep]`.
- `ask` implicitly creates a new workspace database wherever it runs (`cli.py:309-312` prints
  `[peerhub] initialized workspace at ...`). Most other commands *refuse* implicit init
  (`_guard_implicit_workspace_init`, `cli.py:~627-645`) `[code]`. So `cd src && peerhub ask cc "..."`
  creates `src/.peerhub/peerhub.sqlite3`. That's a second, independent store with its own health circuits,
  sessions, transcripts, lessons, and consensus rounds, and nothing warns that the parent directory
  already has one.
- For the agent use case this gets worse: a peer that runs `peerhub ...` from its own working directory
  (a worktree, a subdirectory) reads or writes a different workspace than the one that dispatched it.

Proposal: resolve the workspace git-style: `--workspace` > `PEERHUB_WORKSPACE` (see F1) > nearest ancestor
containing `.peerhub/peerhub.sqlite3` > cwd. `ask` keeps its zero-setup auto-init, but only when *no*
ancestor workspace exists. When it does auto-init, it should print the absolute path. `status` and
`config paths` should report *how* the workspace was found (`explicit|env|ancestor|cwd`), mirroring the
`explicit/env/workspace/default` provenance that `config paths` already reports for config files.

### F3. The operator's own install reports the wrong version (MEDIUM, trivially fixable)

- `where peerhub` → `P:\_sys\env\venv\Scripts\peerhub.exe`. `peerhub --version` → **`0.1.15`** `[probe]`.
  `pyproject.toml` says `0.3.0`. `pip show peerhub` shows an **editable** install of this repo, created
  back at 0.1.15 `[probe]`. The code is live, but `__version__` and `--version` read stale
  `importlib.metadata` (`peerhub/__init__.py:6`, `cli.py:2681`). The `diag` dashboard banner also prints
  that stale version (`telemetry/presenter.py:548-553`) `[code]`.
- So every bug report, transcript, and dashboard screenshot from the primary dogfooding environment is
  mislabeled by three releases. That directly undercuts DIR-004 ("measured, not assumed").

Proposal: single-source the version in-package (`peerhub/_version.py` plus setuptools
`dynamic = ["version"]`). Also make `--version` append `(editable: <path>, git <short-sha>[+dirty])` when
it detects a `direct_url.json` editable install. It's about 20 lines.

### F4. The CLI surface mirrors internal services and hub.py compatibility, not user tasks (MEDIUM)

- 30 top-level nouns and 88 `add_parser` calls `[probe/code]`. At least **six** answer some form of "is
  peer X OK / can I dispatch to it": `status --peer`, `diag`, `health check`, `peer status`, `node
  model-status`, `gate check` `[probe]`.
- `consensus` contains **two parallel voting systems**: `propose` + `vote --actor` (native), and
  `proposal-add` + `proposal-vote --voter` ("legacy-compatible") `[probe]`. A user has to understand
  PeerHub's migration history just to pick the right verb.
- User-facing output still uses the legacy product's name. There are 18 `[HUB] ...`-prefixed print sites,
  and `proposal-add` tells the user **"Vote with: hub.py proposal-vote ..."** (`cli.py:824`). So PeerHub's
  own output points users at a *different tool* `[code]`.
- `peerhub/application/legacy.py` (1,217 lines) survives the "LegacyTranslator fully retired" claim in
  the README. Its docstring says it now "permanently hosts native Command dataclasses", and `api.py` and
  `cli.py` import it. The module name now misleads readers about what's inside `[code]`.

Proposal: give the compatibility surface a name. Either remove `proposal-add/proposal-vote` or move them
under a clearly marked `peerhub compat ...` group. Rename `[HUB]` to `[peerhub]` and drop the `hub.py`
hint. Rename `legacy.py` to something that says what it holds. Merge the six "is peer OK" views into
one `peerhub peer status [PEER]` with `--health/--quota/--gate` facets, and keep the others as aliases for one
release.

### F5. The README is a development journal (MEDIUM)

- About 55 of 173 lines of dated status and correction prose come *before* "Install" `[code]`. A first-time reader
  meets "2026-09-07 regression", "LegacyTranslator", and "gap-2..gap-7" before `pip install`.
- Install Option B pins `v0.1.11`, but the current release is `0.3.0` and PyPI serves 0.3.0
  (`pip index versions peerhub` → 0.3.0 … 0.1.10) `[probe]`.
- "Try it" shows governance commands with 6–9 required flags (`duty claim ... --authority-epoch 1`,
  `session open ... --session-fingerprint fp1`). No human would type these, and they demonstrate F1 rather than a
  workflow.

Proposal: README = what it is (3 lines), install (1 line), 60-second quickstart (`adapter discover` →
`ask` → `diag`), links. Move the status narrative to `docs/STATUS.md` or CHANGELOG. Keep a dated
correction only while it is still true.

### F6. Nobody says who the user is (framing issue behind F1–F5)

No document I read states who PeerHub's primary caller is. The evidence says: for `ask`,
`broadcast`, `diag`, and `status` it's a human. For roughly 25 of the 30 nouns (consensus, task, lesson,
room, duty, session, lock, artifact, role, leadership, feedback, error, alert, …) it's **an AI peer or an
orchestrator**. That's what `hub.py` is used for today. These two audiences need different things:
- Humans need few verbs, good defaults, and prose output.
- Agents need ambient context (F1), a uniform `--json` everywhere, and documented exit codes on every
  command (today only `ask` documents them). They also need a machine-readable schema. The Pydantic-strict `Command` types already
  exist, so a single `peerhub call <method> --input -` (JSON stdin → `ApplicationAPI.submit`) would give
  agents the whole governance surface without anyone hand-maintaining about 540 argparse flags that duplicate
  those types.

Proposal: say it explicitly. **Human surface = argparse nouns, kept small. Agent surface = context envelope +
`peerhub call` over the typed boundary.** Then the two surfaces can be simplified separately instead of
compromising each other.

---

## Part 2 — Comparison against A / B / RATIFIED

Written after reading all three documents and `git show --stat 5d4b90a` (Tier 1/2 shipped: P1, P1b, P2,
P6, P7).

### 2.0 Measurements taken *after* reading (clearly post-hoc, so labelled separately)

- **Vote impersonation, end to end** `[probe]`. In a throwaway workspace (`%TEMP%\ph-vote-*`):
  `consensus propose --proposer cx --required cc,cx --eligible cc,cx`. Then, **from one process**, `consensus
  vote --actor cc --choice agree` and `consensus vote --actor cx --choice agree`. Both exited 0. The round
  moved to `phase=quorum_reached, votes=2/2`, and its state records `votes: {cc: ..., cx: ...}` with no
  field saying who actually cast them. One process manufactured a unanimous quorum. That's F1 as
  observed behavior, not a reading of the code.
- **The P1b notice (shipped in `5d4b90a`) fires on a prediction, not on what actually happened** `[probe]`.
  `peerhub ask nosuchpeer hi -w <fresh tmp dir>` → stderr `[peerhub] initialized workspace at
  <tmp>\ph-p1b-efbe66e9/.peerhub/`, then `unsupported cli_name 'nosuchpeer'`, exit 2. **No
  `.peerhub/peerhub.sqlite3` was created.** `cli.py:309-312` prints whenever `not
  database_path.exists()` *before* execution. So the notice is false on every pre-spawn failure path
  (unknown peer, executable not found, readiness probe failed), which are all exit-2 paths the README
  documents. It also mixes separators (`\...\/.peerhub/`) and echoes `parsed.workspace` verbatim, so the
  default case prints `./.peerhub/` without the directory it actually means.
- The `[HUB]` output prefix is pinned by **8 test assertions in 2 files** (`tests/integration/application/
  test_proposals.py`, `tests/integration/test_artifact_records.py`) `[code: grep]`. It's a deliberate
  hub.py-parity output contract, not an accident. That changes how F4 should be fixed (see 2.4).
- The env-passthrough prerequisite in F1 is unmeasured in the repo too:
  `docs/adapters/checkpoints/codex.md:569` lists "B6 Windows MCP environment inheritance: `PASS | FAIL
  | TEST NEEDED`" `[code]`.
- Count reconciliation: A reports 543 flags and I count 269 `add_argument(` call sites. The two numbers
  are compatible, because loops such as `for action in ("vote","status")` (`cli.py:3045`) and shared
  helpers multiply registrations. RATIFIED accepted A's figure without re-measuring it (see D4).

### 2.1 Agreements

- **README is a journal (A §5, B §2.5, RATIFIED P5):** fully agree. My one addition: install Option B pins
  `v0.1.11` against a 0.3.0 release.
- **`ask -t` defaulting to `READ_ONLY` (A P1, B, RATIFIED P1):** agree. The default is the least-privileged tier.
- **Config/dotdir consolidation needs no changes:** agree, with one refinement. Config files already
  report *how* they were resolved, but the workspace itself doesn't (F2).
- **Short flags, `__main__.py`, `.gitignore` trim:** agree, harmless.
- **Rejecting B's `gov` namespace rename (RATIFIED §1.1):** agree, for an extra reason. Moving
  `consensus vote` to `gov consensus vote` doesn't change what a caller has to *type*. The cost is in the
  flags, not the path.
- **Rejecting `scratch/` automation (RATIFIED §1.3):** agree.
- **P3 tiered `--help`:** fine. Cheap, low value, no objection.

### 2.2 Disagreements (with reasoning)

**D1. The premise "governance verbosity is appropriate for its programmatic callers" is false.**
(A §7.6, RATIFIED §2 item 5, and implicitly the rationale for §1.1.) It fails on two counts:
1. Who the programmatic callers are. When PeerHub replaces `hub.py`, the orchestrator *is* PeerHub,
   which calls services in-process rather than through its own CLI. The processes left typing `peerhub
   consensus vote` are **the AI peers PeerHub itself spawned**, and today they receive no context
   (`environment_delta={}` in all three adapters). They have to parse their identity out of prose and retype it.
2. Where the cost lies. The cost isn't the length of the flags. It's that they're **unverified**
   (2.0, first bullet). "Verbose is fine for machines" would be true if machines went through the typed
   `ApplicationAPI` boundary, whose envelope carries `actor_id` and idempotency. That boundary has no
   production caller.

Recommendation: withdraw this item from the "explicit non-changes" list.

**D2. The workspace ruling (A §2.2 "correct", RATIFIED §1.2 "keep auto-init, add a notice") treats a
state-integrity problem as a visibility problem.** Making the auto-init visible doesn't stop state from
forking. A user in a subdirectory, or a spawned peer in a different cwd, silently gets a separate
governance store (F2). B's instinct was right, but its remedy (go ephemeral, or prompt) was wrong, and
RATIFIED was right to reject that. The fix is in *resolution*, not gating: ancestor discovery plus
`PEERHUB_WORKSPACE`, keeping zero-setup auto-init only where no ancestor workspace exists. Separately,
P1b as shipped is inaccurate (2.0).

**D3. The scoping line was drawn in the wrong place.** All three docs split the work into "user-facing
surface" (in scope) and "core architecture: consensus, capability leases, persistence" (converged, out
of scope). The defect F1 describes lives at the **seam** between those two: how a CLI invocation becomes
an authenticated command. That seam is neither UX polish nor converged core, so it fell between the
reviews. The 9-round review converged on a *design* whose `CommandEnvelope` carries `actor_id`. The
shipped CLI then routed around it. "Already converged" is true of the design and false of the wiring.
This is the **third** time the repo has shown this pattern: the LegacyTranslator (tested, unreachable),
the 2026-09-09 audit (primitives built, and `ask` didn't call them), and now `ApplicationAPI`/`Client`.
A standing lens is worth adding to future reviews: *"for each designed boundary, name its production
caller."*

**D4. RATIFIED's evidentiary standard falls short of DIR-004.** RATIFIED §0 accepts every factual claim
in A and B as "independently plausible against the terminal's own earlier-session familiarity." That's
*declared, unverified*, not measured. It didn't produce a wrong number I could find. It did produce a
wrong *specification*: P1b was written from reasoning ("print on first auto-provision"), and the shipped
implementation prints when nothing was provisioned. A ten-second probe would have caught that.

**D5. P4 (split `cli.py`) should be re-sequenced after the agent-surface decision, not just deferred.**
If governance becomes context-defaulted flags and/or a `peerhub call` over typed commands (F1/F6), a
large share of the ~4,500 lines changes shape or disappears. Splitting them into per-domain modules
first means reorganizing code that's about to be replaced.

**D6. Minor.** RATIFIED §1.4 is titled "additional finding neither proposal raised," but it credits A
for the finding in its first sentence. More substantively, the four-verb consensus overlap is the
*lesser* issue. Both vote paths (`vote --actor`, `proposal-vote --voter`) can be impersonated in the same way.
Adding a help-text callout for the overlap documents the smaller problem and leaves the bigger one alone.

### 2.3 What all three missed

1. **F1: no dispatch context, self-asserted identity, an orphaned typed boundary.** The headline, and
   observed as behavior (2.0).
2. **F2: no ancestor workspace discovery, so state forks by cwd.** The shipped P1b notice gives a false positive.
3. **F3: the dogfood install reports `0.1.15` for 0.3.0 code**, in `--version` and in the `diag` banner.
4. **F4 additions:** PeerHub's own output tells users to run `hub.py proposal-vote` (`cli.py:824`). The
   `[HUB]` prefix is a test-pinned parity contract rather than cosmetic. `legacy.py` survives the
   "fully retired" claim and misleads readers with its name. (A did catch the four-verb overlap.)
5. **F6: nobody named the primary user.** A and B both wrote "human or AI" and then optimized for the
   human.

### 2.4 Recommended changes to the ratified backlog

Keep as ratified: P1, P2, P6, P7 (shipped), P3, P5.

| Change | Tier | What | Test |
|---|---|---|---|
| **Fix P1b** | 1 | Print the notice only *after* the database actually exists (i.e. after a successful bootstrap), using the absolute resolved path. | `ask <unknown-peer>` on a fresh dir → exit 2, **no** notice, no DB. A real first `ask` → notice once, with the absolute path. |
| **Withdraw non-change §2.5** | — | Remove "governance verbosity is appropriate" from the explicit non-changes. Mark it pending D-CTX. | — |
| **P8 (new)** | 1 | Single-source the version (`peerhub/_version.py` + setuptools dynamic version). Make `--version` show `(editable: <path>, git <sha>)` for editable installs. | An editable install whose pyproject version was bumped reports the source version. |
| **P9 (new)** | 1 | Change the `hub.py proposal-vote` hint (`cli.py:824`) to the equivalent `peerhub consensus proposal-vote` invocation. **Leave `[HUB]` as is** (8 test assertions pin it as parity output). Decide it with the shadow-mode comparison work, not in UX polish. | Output text assertion. |
| **P10 (new)** | 3 | Resolve the workspace as `--workspace` > `PEERHUB_WORKSPACE` > nearest ancestor with `.peerhub/peerhub.sqlite3` > cwd. `status`/`config paths` report `explicit/env/ancestor/cwd`. This changes resolution semantics set by the 2026-09-09 ratification, so it **needs a DIR-006 round**. | Running from a subdirectory of a workspace touches the parent DB, and no nested `.peerhub/` appears. |
| **D-CTX (new design round)** | design | Dispatch context envelope (F1), provenance on votes/acks/claims, and governance writes routed through `ApplicationAPI.submit` (or delete that layer honestly). It's architectural and touches consensus integrity, so it's **DIR-006 unanimous plus a DIR-005 high-risk trigger**. **Prerequisite probe:** per-peer env passthrough to tool subprocesses (codex B6 is TEST NEEDED). A cheap first increment: stamp `recorded_by = local-cli:<account>` (already available from `LocalProcessCallerIdentityProvider`) on each governance write. That doesn't prevent impersonation, but it makes it auditable. | Impersonation probe from 2.0 is rejected (token mismatch) or recorded as `asserted`. |
| **Re-sequence P4** | 4 | After D-CTX decides the agent surface. | — |

Nothing in the backlog that's already shipped needs reverting. The one shipped item that needs a fix is
P1b, which is small. The substantive change is to the *non-changes list* and the *scope line*, not to
the Tier 1/2 work.
