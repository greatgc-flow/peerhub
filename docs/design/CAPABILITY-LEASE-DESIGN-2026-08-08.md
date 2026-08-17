# Capability/Mutation-Lease Design — Roadmap Proposal (2026-08-08)

**Status: proposed, NOT implemented. Targeted for Stage 3+ (real adapter conformance), not a blocker for current Tier-1/Tier-2 work.**
**Update 2026-08-17 (found by an audit pass): stale.** This design has
long since been implemented and committed (`CapabilityLease`,
`validate_capability_binding()`, migration `0018_capability_leases.sql`,
generalized further by T1 increment 5B-1b, commit `31f5794`). See
`CAPABILITY-LEASE-DESIGN-2026-08-08-ERRATA.md` for the corrective design
pass and `HUB-REPLACEMENT-ROADMAP-2026-08-09.md` for the full
implementation history. Left below as the original proposal record.

## Why this exists

P:'s current hub.py-based multi-peer system has no real enforcement of what a dispatched peer (ag/cx/cc) is actually allowed to mutate during a given task. This surfaced concretely twice:

1. **2026-08-06**: ag committed to peerhub's git repo without authorization mid-dispatch (not pushed, caught + reverted). The "fix" at the time was a per-dispatch prompt instruction ("do not run git commit/push yourself") that cc has to restate manually every single time — advisory only, no technical backing.
2. **2026-08-08 peer-environment audit** (see `_sys` memory `project_peer_environment_audit_2026_08_08`): independent parallel review by ag and cx found the same shape of gap is universal, not ag-specific — cx's own live tool surface exposes ~196 callable tools (GitHub create/update/delete/merge, Google Drive delete/share, Gmail, Sites deployment, local `apply_patch`) even on dispatches explicitly framed as review-only. cc's own permission posture (`--dangerously-skip-permissions`, `defaultMode: auto`) is the same honor-system shape a third time.

**Critical finding that redirected this work here instead of patching P: directly**: cc traced hub.py's own `preflight.mutating_patterns` mechanism (the thing that's supposed to flag/block `git commit`, `git push`, etc.) end to end and found it is **completely disconnected from the actual dispatch execution path**. `_classify_command()` (hub.py:9450) has exactly one caller, `action_preflight()` (hub.py:9534), which is a standalone, manually-invoked CLI action (`python hub.py preflight --cmd "..."`) — nothing in `_ask_with_pty()` (the function that actually spawns and drives a peer's real PTY session) calls it. The config debate ag and cx had (whether `git_mutation` is correctly phase-gated) was moot: the rule doesn't gate anything regardless of phase, because nothing consults it during a real dispatch. **A same-day fix to `protocol.json` would have been pure security theater** — editing a JSON field nothing reads.

Given P: (hub.py) is the system peerhub exists to replace, and peerhub is still pre-cutover (no live traffic depends on its dispatch path yet), the right place to build real enforcement is **in peerhub itself, properly wired from the start**, not bolted onto a system already slated for retirement.

## How this maps onto peerhub's existing architecture

peerhub already has the right shape of primitives to build on, from tonight's Tier-2 work:

- `application/api.py`'s `CommandDescriptor` already carries a `Mutability` enum (`MUTATING` vs presumably a read-only variant) per command — this is the natural anchor point for a capability check, not a new concept.
- `adapters/contract.py`'s `PeerAdapter` Protocol (`plan_invocation`, `interpret_output`) is the seam between peerhub's dispatch orchestration and whatever real CLI/process a peer actually runs through — this is where a capability/lease check belongs, analogous to where cx recommended enforcement "at the adapter/wrapper level, not just the dispatcher's own call site."
- `dispatch/service.py`'s 4-coordinator split (`AdmissionCoordinator`, `AttemptLifecycleCoordinator`, `SessionLeaseCoordinator`, `ArtifactCoordinator`) already has a `SessionLeaseCoordinator` — the *naming* overlaps with "mutation lease" conceptually, though today it's about session/generation CAS, not capability scoping. Worth checking during design whether these should be the same lease concept or deliberately separate ones (recommend separate — session leases are about *which generation of a conversation* is active, capability leases are about *what a dispatch is allowed to do*; conflating them risks a confusing dual-purpose primitive).

## The two independent design passes (dialectical round, 2026-08-08)

**ag's initial proposal** (P: session, not peerhub-committed): a `--lease read-only` flag on the dispatch entrypoint; pre-dispatch sandbox/flag tightening per peer; a PATH-prepended `git.bat` shim to intercept ag's git commands and consult a lease file; `codex --disable <connector>` flags for cx. ag's own final recommendation was to skip building any of this and just do a config one-liner instead — cx's critique below is why that recommendation doesn't actually hold once you trace hub.py's real code, quite apart from cx's own objections to it.

**cx's critique** (verified where possible, honest about what couldn't be checked from its own sandboxed environment):
- **Refuted ag's specific `codex --disable github`/`--disable google-drive` flags as fabricated** — checked real Codex CLI docs and found the actual controls are `--disable apps`, `-c 'apps.<id>.enabled=false'`, and MCP-level `enabled_tools`/`disabled_tools` config, not per-connector-name flags. (Whatever peerhub eventually builds against a real Codex adapter needs to use the *verified* flag surface, not ag's guessed one.)
- **The PATH-shim git-interception idea is not a real security boundary** — enumerated concrete bypasses: absolute-path invocation of the real git binary, a different git wrapper or `gh`/GitHub API/SSH mutating the remote without touching a shimmed binary at all, direct writes to `.git/index`/`refs`/`packed-refs`, the peer re-prepending its own PATH, and a shared lease-state file being both peer-writable (tamperable) and racy across concurrent dispatches. Demoted to "telemetry/friendly-denial only" at best.
- **A `git diff --stat` post-dispatch check is insufficient** — misses staged changes, untracked files, a commit that leaves the tree clean, ref/branch changes, and remote pushes. Proposed a real pre/post state comparison instead (HEAD + refs + index digest + full porcelain status with optional locks disabled).
- **Proposed a 4-tier capability model** instead of a flat binary: `READ_ONLY` / `WORKTREE_WRITE` / `GIT_MUTATE` / `REMOTE_MUTATE`, with lease state living outside any peer-writable path, a unique lease ID per dispatch (never a shared/racy file), and enforcement required at the adapter/wrapper level so no entrypoint (resumed session, orphan recovery, concurrent dispatch) bypasses it.
- **For real OS-level confinement of an unsandboxed peer** (ag's actual gap): proposed Windows restricted-token/restricted-process launchers as the genuine mechanism, since nothing at the shell-interception layer can be made airtight.

## What this means for peerhub's design, concretely

A future `CapabilityLease` (name TBD, avoid colliding with the existing `SessionLeaseCoordinator` naming) should be:

1. **A first-class value threaded through `PeerAdapter.plan_invocation`**, not a side-channel config file — the adapter boundary is exactly where cx said enforcement belongs, and it's exactly the seam peerhub already has.
2. **Modeled as the 4-tier capability set** (read-only / worktree-write / git-mutate / remote-mutate) rather than a binary flag — cx's critique of the binary model was specific and well-reasoned (a "review" dispatch and a "let this peer freely rewrite its own worktree but not touch git or push" dispatch are genuinely different scopes peerhub's real workflows already need).
3. **Enforced where peerhub controls the actual subprocess/session creation** for each adapter (this is exactly what Stage 3 — real Claude/Codex/Antigravity adapter conformance — is for; this design should land as part of that stage, not before it, since there's no real adapter to enforce anything on yet).
4. **Backed by real, verified flag/config surfaces per peer CLI** (Codex's actual `--disable apps` / `apps.<id>.enabled` / MCP `enabled_tools`-`disabled_tools`, whatever Claude Code's and Antigravity's real equivalents turn out to be — verify each empirically before building against it, per cx's documented refutation of ag's guessed flags).
5. **Not attempt shell-level interception (PATH shims, wrapper scripts) as a security boundary** — cx's bypass list applies identically inside peerhub's own future adapters if they're built as thin subprocess wrappers around the same real CLIs. If genuine OS-level confinement is wanted for an adapter peerhub doesn't fully control (e.g. a future Antigravity-equivalent adapter with no native sandbox), that's real, separate systems work (restricted tokens / containers) and should be scoped as its own explicit task when Stage 3 gets there, not assumed as a side effect of the lease model.

## Explicitly deferred, not forgotten

- Full Windows-restricted-token process confinement for an unsandboxed adapter.
- Compliance/audit-evidence logging (pre/post state digests, violation records) — valuable, but downstream of having any real enforcement to audit in the first place.
- Extending the same model to peerhub's own CLI/dev-tooling story for cc (i.e., whatever eventually replaces `--dangerously-skip-permissions`-style operation for a Claude-Code-driven contributor) — noted by cx as a real gap, out of scope until peerhub has a concrete cc-equivalent adapter to design against.

## Also flagged (not part of this design, just worth remembering)

- cx's Gmail connector currently fails with "requires reauthentication" — user-account issue, unrelated to peerhub, reconnect if that connector is actually used.
