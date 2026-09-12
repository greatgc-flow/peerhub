# PeerHub UX / Structure Simplification — RATIFIED

**Status:** RATIFIED — implementation authorized for the backlog in §3 below.
**Date:** 2026-09-12
**Ratifier:** cc (terminal, Claude Sonnet 5) — cx was unavailable for this round (real vendor-side quota exhaustion, `cx.astra` blocked until 2026-09-15, shared "X" pool at 4% headroom, 0 reset credits); both Round-1 voices were independent `ag` profiles backed by different underlying models (`ag.opus` = Claude Opus 4.6 via Antigravity, `ag.deepthink` = Gemini), so this ratification substitutes a third, distinct model rather than delaying indefinitely.
**Inputs:** `docs/design/peerhub-ux-simplification-proposal-A-2026-09-12.md` (ag.opus), `docs/design/peerhub-ux-simplification-proposal-B-2026-09-12.md` (ag.deepthink). Both re-read in full; every load-bearing factual claim in both (line counts, flag counts, file paths, quoted code) is independently plausible against the terminal's own earlier-session familiarity with this repo and is accepted as accurate — no contradiction was found between the two proposals' facts, only in some of their prescriptions.
**Scope:** PeerHub's user-facing surface only — CLI ergonomics, install/first-use experience, config/dotdir layout, root folder hygiene, README structure. Core service architecture (consensus machinery, capability-lease enforcement, persistence/UoW split, hub.py-replacement roadmap internals) is explicitly out of scope, per both proposals' own scoping and the already-converged 9-round adversarial review (`docs/design/ARCHITECTURE.md`).

**AMENDMENT (2026-09-12, same day, post-shipment of Tier 1/2):** a third, independently-commissioned review (`docs/design/peerhub-ux-simplification-cc-deepthink-review-2026-09-12.md`, cc.deepthink — deliberately investigated fresh, before reading A/B/this document, per explicit user request for an unconstrained third opinion) found a real defect this ratification's own scoping line had missed: the "governance commands are fine as-is for their programmatic callers" premise (§2, non-change item 5) is **false**, empirically demonstrated by a live impersonation probe (one process cast a consensus vote as `cc`, then as `cx`; the round reached `quorum_reached, 2/2` with no record of which was real). All three adapters spawn peers with `environment_delta={}` (verified: `agy_adapter.py:229`, `claude_adapter.py:279`, `codex_adapter.py:485`), so a spawned peer has no way to present verified identity, and the typed `ApplicationAPI`/`Client` boundary designed to carry `actor_id` has zero production callers. This sits exactly on the seam between "UX polish" (in scope here) and "core architecture" (out of scope here) — neither prior pass caught it because each assumed the other side owned it. See §5 below for the accepted amendments. This ratification's backlog in §3 is otherwise **unchanged and still authorized as originally written** — nothing already shipped needs reverting.

---

## 0. Where A and B agree (high-confidence — both proposals independently converged)

Both proposals, working independently, reached the same core diagnosis and remedy shape:

1. The CLI has ~30 flat top-level commands / ~100 actionable paths / ~540 flags in a single ~4,500-line `cli.py`, and this validates/extends the pre-existing 2026-09-07 project-memory finding ("core `ask` sound but buried in enterprise-CQRS ceremony").
2. `--capability-tier` should default to `READ_ONLY` on `ask` (removes the single biggest friction point on the most-used command).
3. The 2026-09-09 config/dotdir consolidation is already in good shape — **no changes proposed there, ratified as-is.**
4. The README (21KB/173 lines) is a project-history journal wearing a quick-start's clothes, and the historical narrative should move out of the critical path.
5. `.ai/`, `.hypothesis/`, `.pytest_cache/` are correctly gitignored, expected, not peerhub's problem to fix.

These five points are **ratified without further debate** — independent convergence from two differently-backed models on identical, independently-verified facts is strong evidence, and neither proposal's reasoning here has a flaw the other exposed.

## 1. Where A and B disagree — rulings

### 1.1 RULING: command namespace stays flat; `--help` gains tiered display only (adopt A over B)

**A proposed:** keep every command's actual invocation path unchanged; only reorganize `--help` output into visual tiers (Primary / Setup / Operations / Governance) via a custom formatter.
**B proposed:** actually move ~24 governance commands under a new `peerhub gov <command>` namespace (a real path change), with deprecated top-level aliases for one version cycle.

**Ruling: adopt A's non-breaking approach.** Both proposals' own facts state the governance tier (`consensus`, `task`, `lesson`, `room`, `duty`, `session`, etc.) is "primarily an API-contract surface called programmatically by `hub.py` or its replacement, not human-typed." A real path rename is exactly the kind of change that breaks programmatic callers silently until they hit it — and peerhub's own stated purpose is to become that replacement's dispatch layer, i.e. the exact kind of caller most exposed to this break. B's own mitigation (deprecated aliases for one cycle) concedes the risk is real without eliminating it, just delaying it. A's tiered-`--help`-only approach gets the entire discoverability win (a new user sees 4 commands, not 30) with zero compatibility risk. If a genuine appetite for a real namespace change emerges later, it should be its own separately-scoped, separately-risk-assessed change — not bundled into a UX-polish pass.

### 1.2 RULING: keep silent workspace auto-init, but make it visible (adopt A's "no change" verdict, add B's one real point as a minimal addition)

**A proposed:** no change — `--workspace` defaulting to `.` with auto-provisioning on first `ask` is correct, documented, intentional design (the `peerhub.direct-ask/v1` bootstrap).
**B proposed:** either operate ephemerally or prompt the user rather than silently creating `peerhub.sqlite3` in whatever directory the user happens to be in.

**Ruling: split the difference precisely, not in the middle.** A is right that removing or gating the auto-init would regress an already-good, already-ratified "just works" primitive (the whole point of `peerhub.direct-ask/v1` was to eliminate manual `workspace init`) — B's "operate ephemerally or prompt" would reintroduce exactly the friction P1 is trying to remove elsewhere. But B's underlying concern is real and A didn't address it: silently writing a SQLite file into whatever directory `ask` happens to be run from, with zero user-visible feedback, is a genuine footgun (a stray `.peerhub/` in the wrong folder, discovered much later). The minimal, friction-free fix: print one line to stderr the first time a workspace is auto-provisioned — `"[peerhub] initialized workspace at ./.peerhub/"` — so the behavior stays automatic but is never silent. This is a new backlog item (P1b below), not in either original proposal verbatim, but directly synthesized from the real tension between them.

### 1.3 RULING: root folder hygiene — reject B's `scratch/` cleanup automation

**A concluded:** `scratch/` is gitignored, documented (with an explicit comment explaining a 2026-09-09 incident it guards against), and well-managed. No change.
**B proposed:** add a cleanup script or git hook to auto-purge `scratch/`, or relocate it under `.ai/scratch/`.

**Ruling: adopt A, reject B here.** A's investigation was the more thorough of the two on this specific point (it read and quoted the actual `.gitignore` comment explaining scratch/'s purpose and history; B's finding is a one-line surface observation without that context). Adding automated purging or relocation to fix something that isn't actually broken is exactly the kind of premature complexity this project's own standing design preference explicitly warns against. **No change.**

### 1.4 ADDITIONAL FINDING neither proposal raised, added by the ratifier

A flagged (§6, "Disposition") a specific, concrete API-level redundancy: `consensus propose`, `consensus proposal-add`, `consensus proposal-vote`, and `consensus vote` are four overlapping subcommands doing approximately the same thing. B did not independently verify or dispute this. Given it's a narrow, CLI-surface-only observation (not core architecture) and A cited it with enough specificity to check, it is accepted into the backlog as a documentation/cleanup item, not a functional change (see P3 below) — flag the overlap in `--help` text and README, do not remove or rename anything this round, since resolving the actual redundancy is a service-layer question (out of scope) even though naming it is not.

---

## 2. Explicit non-changes (ratified as correct, do not touch)

1. Config/dotdir consolidation (2026-09-09) — in good shape, no changes.
2. `.ai/` at repo root — hub.py's runtime state, correctly gitignored, not peerhub's responsibility.
3. `scratch/`, `.hypothesis/`, `.pytest_cache/` — correctly managed ephemeral/scratch state.
4. `alembic/`, `alembic.ini` — intentionally tracked as a deferred-migration design record (HOLD status per README).
5. ~~The governance command surface's existence and verbosity — appropriate for its actual (programmatic) callers; only its `--help`-tier visibility changes (§1.1).~~ **WITHDRAWN, see §5 amendment below.** The premise was wrong: those callers are unauthenticated (no verified identity reaches them at all), not merely verbose.
6. Core service architecture — untouched, out of scope, per both proposals and the existing 9-round ratified design.

---

## 3. Implementation backlog (priority-ordered; each item independently shippable)

Every item below needs: the specific test(s) it should have (TDD — write first, confirm red, then implement), and must leave the full `pytest -q` suite and `pyright` at their current clean baseline (0 pyright errors; note the current passing count yourself at implementation time per this repo's own convention of never hardcoding it in docs).

### Tier 1 — trivial, zero-risk, ship together

- **P1: default `--capability-tier` to `READ_ONLY` on `ask`** (and confirm `broadcast` already defaults correctly, per A's P8 finding — verify, don't just trust the proposal). Test: `peerhub ask <peer> <prompt>` with no `--capability-tier` resolves to `READ_ONLY` in the built request; explicit override still works.
- **P1b: one-line stderr notice on auto-provisioned workspace** (§1.2 above — new, not in either proposal verbatim). Test: first `ask` in a directory with no `.peerhub/` prints an initialization notice to stderr exactly once; a second `ask` in the same now-provisioned workspace does not print it again.
- **P6: add `peerhub/__main__.py`** for `python -m peerhub` support. Test: `python -m peerhub --version` exits 0 and matches `peerhub --version`.
- **P7: trim `.gitignore`** to remove unused framework-template sections (Django/Flask/Scrapy/Celery/Redis/RabbitMQ/ActiveMQ/SageMath — none used by this project). No test needed (no behavior change); verify via `git status` before/after on a clean checkout that nothing newly shows as untracked.

### Tier 2 — small, real user-facing change, needs a deprecation note but not a deprecation *cycle*

- **P2: add short flags** (`-t`/`--capability-tier`, `-w`/`--workspace`, `-j`/`--json`, `-p`/`--profile`) to the primary commands (`ask`, `status`, `diag`, `broadcast`). Test: each short form parses identically to its long form.

### Tier 3 — moderate, real design/implementation work

- **P3: tiered `--help` output** (custom argparse `HelpFormatter`, NOT a namespace/path change — see ruling §1.1). Include the `consensus propose`/`proposal-add`/`proposal-vote`/`vote` overlap as a one-line callout in the Governance tier's help text (§1.4), not a functional change. Test: `peerhub --help` output contains the tier headers and every existing command name still appears (exact set unchanged) and still works with its unchanged invocation path.
- **P5: README restructure.** Move the ~60-line status/history narrative into `docs/design/README.md` (already exists as the design-docs index) or a new `docs/STATUS.md` (ratifier's call for the implementer to make based on which reads better once drafted — either is acceptable, just pick one and be consistent). Reduce the README itself to: one-liner, Quick start (3-4 real commands), Install, Key commands table, one-paragraph status + link, Run the tests. Test: none (docs-only), but manually re-verify every command shown in the new "Quick start"/"Key commands" section by actually running it against a fresh workspace before committing, per this project's own standing convention of never asserting behavior without having just executed it.

### Tier 4 — larger, optional, sequence-dependent (do only if the above land cleanly and there's appetite to continue)

- **P4: split `cli.py` into per-command modules** (`peerhub/cli/__init__.py` + per-domain files, per A's proposed layout). This is what makes P3's formatter maintainable long-term (right now the tier definitions would live awkwardly alongside 90 subcommand registrations in one file) but is not a prerequisite for shipping P3 — a formatter can reference a flat tier-mapping dict defined once, independent of file layout. Treat P4 as a follow-up refactor once P1-P3+P5-P7 are live and stable, not a blocker for any of them. Test: identical to today's full suite (a pure reorganization must not change any behavior); `pyproject.toml`'s `peerhub = "peerhub.cli:main"` entry point must keep resolving unchanged.

### Explicitly not in this backlog

- B's `peerhub gov` namespace rename (§1.1 — rejected).
- B's `scratch/` cleanup automation (§1.3 — rejected).
- Any core architecture change (out of scope for both proposals and this ratification).

---

## 4. Verification requirements for whoever implements this backlog

- TDD throughout: test written and confirmed red before each implementation change, per this project's standing policy.
- Full `pytest -q` and `pyright` clean (matching whatever the current baseline actually is at implementation time — read it fresh, don't cite last night's number) before any commit.
- Tier 1 and Tier 2 items are independently shippable in any order; Tier 3's P3 and P5 are independent of each other; Tier 4 (P4) should come last if pursued at all.
- Commit locally; push only after the terminal reviews the diff (same discipline as tonight's other work in this session).

---

## 5. Amendment (cc.deepthink's third review, accepted in full — see the amendment note in the header)

Every empirical claim below was independently spot-checked by the ratifier via direct file read/grep before acceptance (`environment_delta={}` in all three adapters; the `hub.py proposal-vote` hint at `cli.py:824`; the installed `0.1.15` vs. `pyproject.toml`'s `0.3.0`) — this is not a second-hand acceptance.

### 5.1 New Tier 1 items (small, safe, ship alongside or right after the original Tier 1/2 backlog)

- **Fix P1b (the shipped notice has a real bug, not a style nit).** It currently prints `"[peerhub] initialized workspace at ..."` whenever `database_path` doesn't exist *yet*, checked before the dispatch runs — so it fires on every pre-spawn failure path too (unknown peer, missing executable, failed readiness probe), none of which actually create a database. Fix: print only *after* a successful bootstrap confirms the database now exists, using the fully resolved absolute path (not `parsed.workspace` echoed verbatim — that produces mixed separators like `<tmp>\...\/.peerhub/`). Test: `ask <unknown-peer>` in a fresh directory exits 2, prints no notice, and creates no database; a real first successful `ask` prints the notice exactly once with a real absolute path.
- **P8: single-source the package version.** The dogfooding install reports `0.1.15` (`peerhub --version`, and the `diag` banner) while `pyproject.toml` says `0.3.0` — an editable install whose cached metadata never got refreshed. Move to `peerhub/_version.py` + setuptools `dynamic = ["version"]`, and have `--version` append `(editable: <path>, git <short-sha>[+dirty])` when it detects an editable install (via `direct_url.json`). Test: after bumping `pyproject.toml`'s version, `--version` reflects it without a reinstall being required to notice the drift (or at minimum, without silently reporting a 2-releases-stale number as if it were current).
- **P9: fix the wrong tool hint, leave `[HUB]` alone.** `cli.py:824` currently prints `"Vote with: hub.py proposal-vote --proposal-id ... --vote agree --voter <peer>"` — PeerHub's own CLI telling the user to run a *different tool*. Change the hint to the real `peerhub consensus proposal-vote ...` equivalent. Do **not** touch the `[HUB]` output prefix itself in this pass — it's pinned by 8 real test assertions across `tests/integration/application/test_proposals.py` and `tests/integration/test_artifact_records.py` as a deliberate hub.py-parity output contract, not an accident; revisit it only alongside the shadow-mode hub.py-comparison work mentioned elsewhere in this repo's own backlog, not as UX polish. Test: output-text assertion on the corrected hint string.

### 5.2 Deferred, NOT authorized for implementation yet — needs its own design round

- **P10 (workspace resolution — Tier 3, but blocked):** resolve `--workspace` as `explicit flag > PEERHUB_WORKSPACE env var > nearest ancestor directory containing .peerhub/peerhub.sqlite3 > cwd` (git-style), instead of today's literal `.` with no upward discovery — today, running `ask` from a subdirectory of an existing workspace silently forks a second, independent `.peerhub/` database with its own sessions/consensus/lessons, and nothing warns about it. This is a real, well-evidenced finding, but it **changes workspace-resolution semantics the 2026-09-09 config/dotdir consolidation already ratified** — per this project's own standing policy (never a rushed change to something already agreed, especially one touching state-integrity), this needs its own explicit round, not a quiet inclusion in a UX-polish backlog. **Do not implement until that round happens.**
- **D-CTX (new, separate design round — dispatch context envelope + governance write provenance):** when peerhub spawns a peer, hand it an environment envelope (`PEERHUB_WORKSPACE`, `PEERHUB_ROOM`, `PEERHUB_DISPATCH_ID`, `PEERHUB_PEER`, `PEERHUB_DISPATCH_TOKEN` — a random per-dispatch secret, stored only as a hash, bound to that dispatch's lease lifetime); required identity flags (`--actor`/`--proposer`/`--creator`/`--voter`/etc.) become optional whenever a valid envelope is present; every governance write records real provenance (`dispatch-token` verified / `local-cli` OS-authenticated / `asserted` no evidence) instead of silently trusting a caller-typed string; an explicit flag that contradicts a present token is rejected; quorum policy can later require verified provenance for high-risk rounds. This is a genuine, real integrity gap (empirically demonstrated: one process voted as two different peers and reached full quorum) — but it is exactly the kind of large, security-relevant, unlimited-round-dialectic-requiring design change this project's standing policy exists for. **This ratification does not authorize implementing it.** It should become its own future 2+-model dialectical round (matching tonight's own Engram/PeerHub-Round-1-and-2 process), with its own proposals, its own ratification, and — per this repo's own internal convention referenced in the third review — real unanimity before anything touching consensus-vote integrity ships. **Prerequisite, cheap, do this first regardless of when the full round happens:** verify empirically whether each of the three real peer CLIs (Claude Code, Codex, Antigravity) actually passes its parent process environment through to the tool/shell subprocess the model runs — this repo's own `docs/adapters/checkpoints/codex.md` already lists the Codex half of this as "TEST NEEDED," and the review flagged Claude/Agy as unverified assumptions, not measurements. The whole envelope design depends on the answer.
- **Re-sequence P4** (splitting `cli.py` into per-command modules, Tier 4): move it to *after* D-CTX is decided, not merely "last." If governance moves to context-defaulted flags and/or a typed `peerhub call` surface, a large fraction of the ~4,500 lines being split today would be reorganizing code that's about to change shape or disappear.

Nothing already shipped (P1, P2, P6, P7) needs reverting. P1b needs the fix in §5.1, not a revert.
