---
status: PLANNING — no implementation has started
date: 2026-09-02
title: Phase 2 — Full Engram/PeerHub Separation Master Plan
---

# Phase 2: Full Engram/PeerHub Separation — Master Plan

> **Read this first** for the whole "make Engram and PeerHub fully
> independent packages" effort. It exists so the situation and plan are
> documented before more work happens, per the user's explicit instruction
> today ("전체 상황을 파악하고 계획을 세워서 문서화 한 후 진행"). It ties
> together five research/critique documents produced today (linked below)
> rather than restating their content — read them for full citations and
> reasoning; this doc is the map, not the territory.

> **UPDATE (2026-09-03): Gates 1, 5, and 6 are RATIFIED.** The Engram
> "diet plan" (ownership matrix + migration ledger + phased deletion/
> release plan) went through 8 rounds of research→critique→revision
> (7 by `cx`, round 8's final critique terminal-performed after `cx`
> became genuinely unavailable — see
> `2026-09-02_engram-diet-plan-v8-critique-terminal.md` in the Engram
> worktree's session-doc directory) before being ratified with one minor,
> non-blocking follow-up tracked for implementation time (a replacement
> dual-instance test under the new `ENGRAM_ROOT` model). The architecture
> itself never changed across all 8 rounds — every round's findings
> narrowed from conceptual to increasingly mechanical. The final ratified
> plan is `2026-09-02_engram-diet-plan-v8.md` (all prior `v1`-`v7` +
> critique documents in the same directory are its full audit trail, not
> separately normative). Gate 2 (PeerHub autodetection, specifically its
> discovery-sweep half) remains the one open design item — see §4 below.
> **No implementation has started on any gate** — this ratification
> authorizes starting Increment A's TDD, pending the user's own review and
> explicit go-ahead.

## 1. The goal, as stated by the user (2026-09-02)

- Engram's existing hub.py-related package is deleted entirely and replaced
  by PeerHub. Compatible-or-better via PeerHub is fine.
- **PeerHub's purpose: AI-to-AI communication and collaboration**, including
  auto-detecting installed AI CLIs.
- **Engram's purpose shrinks to "portable dev environment," full stop.**
  Everything AI-CLI-related beyond install/uninstall/update/status-check is
  deleted from Engram (migrated into PeerHub first if PeerHub still needs
  the capability, then deleted from Engram).
- **`P:\` (branch `stable/hub-py-restored`) is never modified again** — its
  current state is the final, frozen checkpoint.
- Work happens on **Engram's `main` branch** (not `stable`) and on
  **`P:\workspace\peerhub`** (its own repo, branch `main`) — both become
  fully independent packages.
- End state: install Engram, then install AI CLIs + PeerHub on a portable or
  plain local machine → get an AI-collaboration environment equivalent to
  what `P:\` provides today.
- **No implementation before the design is fully detailed and unanimously
  ratified** through the standing dialectical process (research → critique
  → ratify). Test suite must be MECE and E2E-verified, including real
  CLI-level measurement and per-CLI statusline consistency — not mocked.

## 2. Current situation (as of 2026-09-02, independently verified)

Three repositories/branches are in play, and it is important not to
conflate them:

| Name | Location | Branch | State |
|---|---|---|---|
| **stable** | `P:\` | `stable/hub-py-restored` | **Frozen.** Already 0 commits ahead/behind `origin`. Contains the live, working `hub.py`-based system. Never touch again. |
| **Engram main** | `D:\Engram&Peerhub\engram-main-worktree` (isolated git worktree, sibling to `P:\`'s own repo root, never nested inside it) | `main` | **Dormant since 2026-08-19.** An earlier session already removed `hub.py` and the legacy coordination cluster here (commit `6b50945`, 207 files, -57,403 lines) and integrated a pinned "PeerHub v0.1.7" — but this was never adopted as the live checkout; `P:\` was reverted to `stable` the same day and `main` has had zero commits since (still at `b920574`). |
| **PeerHub** | `P:\workspace\peerhub` | `main` | **Far advanced, actively developed.** 441 commits past the reference Engram `main` pinned (`7a5f939` → `5b7ce5a`). This session's LEGACY_CATALOG marathon (2026-08-27 → 2026-09-02) brought the hub.py-action-translation layer to 71/90, permanently waiving the remaining 19. Independent of the Engram separation question — PeerHub was already being built as its own product. |

`stable` gained 8 commits since the `main` fork; all 8 are hub.py/vendor-
specific or superseded — **none belong on `main`** (verified,
gap-analysis §1).

## 3. The eight gates — status as of today

The gap-analysis (§ "Recommended pre-implementation gates") named 8 gates.
Status after today's research/critique/reconciliation rounds:

| # | Gate | Status |
|---|---|---|
| 1 | **Ownership matrix**: Engram=environment lifecycle, PeerHub=every AI-provider/collaboration/routing/session/health/governance capability | **DONE (2026-09-03).** Landed as Increments A–D (`20a23f4`, `e52ec4a`, `1b7d7d9`, `e599b37`). Engram retains AI-CLI *tool lifecycle* (install/update/status-check as an ordinary managed binary); PeerHub owns *using* those CLIs as peers (adapters, invocation, routing, sessions, health, governance, discovery). Full detail in `2026-09-03_separation-completion-backlog.md`. |
| 2 | **PeerHub autodetection design** | **Parked, not open.** The discovery-sweep half was reviewed and its runnable-admission (Lane 2) form was explicitly **REJECTED** by the 2026-09-05 final security review (`PHASE1-MANIFEST-SCHEMA-V2-FINAL-SECURITY-REVIEW-2026-09-05.md`) — TOCTOU, junction/symlink swap, PATH re-resolution, Unicode-homoglyph collision, and ACL-scope gaps remain unresolved for anything runnable. The user explicitly chose not to build even the inert candidate-discovery subset. Built-in Lane 1 (`cc`/`ag`/`cx` autodetection) already ships via `peerhub/adapters/discovery.py`. Do not reopen Lane 2 design work without a fresh, unanimously-ratified security round starting from inert-scan-only. |
| 3 | **PeerHub autodetection implementation + measured release** | **Parked with gate 2.** No implementation for third-party (Lane 2) adapters is authorized. Built-in Lane 1 is already implemented and shipped. |
| 4 | **Independent installation contract** (does Engram link to or install PeerHub?) | **DONE (2026-09-06).** Re-litigated and ratified as its own explicit decision after the user asked why Engram pinned peerhub's exact version at all. Axis A (install-time provisioning): Engram no longer lists peerhub in `runtimes.json`'s tools catalog; the `pip_tool` install mechanism (built specifically for peerhub, used nowhere else) was deleted outright, with a permanent boundary-regression test (`test_runtime_catalog_does_not_manage_peerhub`) guarding against reintroduction. Axis B (post-install runtime discovery): Engram does not invoke `peerhub adapter discover` at all, not even opportunistically — preserving a command-contract dependency on an unrelated PATH-resolved executable was judged inconsistent with "genuinely independent" and with this session's own PATH-resolution TOCTOU findings elsewhere. README documents `pip install peerhub` (now real via PyPI) purely as a user-run, independent step. |
| 5 | **Migration ledger** (exact facts moving to PeerHub, explicit waivers for the rest, never migrate credentials/host trust) | **DONE (2026-09-03).** The full ledger — exact directive digests, consumers, receipt preconditions, and waivers (credentials, host trust, transient state, `.ai` workspace, legacy hub.py logs) — was instantiated in the ratified v8 diet plan and landed with Increments A–D. Engram's own statusline implementation was deleted outright (not migrated) since PeerHub already had its own. |
| 6 | **Engram deletion plan**, in reviewable increments, protected by a zero-AI-ownership contract | **DONE (2026-09-03).** Landed as Increments A–D, each keeping `test_contracts.py`'s boundary green throughout via a shrinking interim allowlist rather than a single big-bang deletion. `test_boundary_imports.py` now enforces the final, strict zero-AI-ownership invariant. |
| 7 | **Packaging/doc reconciliation** (Winget metadata, root docs) after the code boundary is final | **DONE (2026-09-03)** for the original scope — landed as `8a0b267` after Increment D. **Reopened narrowly (2026-09-06)** only because of gate 4's new decision: Engram's next release/winget submission needs to be rebuilt from the post-pip_tool-removal source so the shipped package/manifest no longer references peerhub. PeerHub's own PyPI publication (tonight) is unrelated to this gate. |
| 8 | **Clean-room validation**, only in the isolated worktree + PeerHub repo, never touch frozen `stable` | **Ongoing discipline, correctly followed** — worktree used throughout, `P:\` status checked before/after every session. Note for precision: `P:\` itself is not literally pristine (it sits on `stable/hub-py-restored` with its own explicit, narrow, user-authorized operational commits unrelated to this separation) — the actual invariant is "no Engram-separation edit, test, or packaging input ever uses the frozen `P:\` checkout," which has held. |

## 4. What's next: nothing, by explicit choice

As of 2026-09-06, every gate above is either DONE (1, 4, 5, 6, 7) or
explicitly PARKED by the user's own decision (2, 3) — not blocked on
missing design work, but deliberately not being pursued. Gate 8 is an
ongoing discipline, not a deliverable.

Gate 2/3's Lane 2 (third-party, runnable adapter admission) design is
parked because its 2026-09-05 final security review found real,
unresolved exploit classes (TOCTOU, junction/symlink swap, PATH
re-resolution, Unicode-homoglyph collision, ACL-scope gaps) and the user
chose not to build even the inert candidate-discovery subset given that.
Built-in Lane 1 (`cc`/`ag`/`cx` autodetection) already ships.

**If Lane 2 is ever explicitly reopened later**, do not resume from this
doc's old §4 sketch (a discriminated result-type set, a registry.py
collision-binding mechanism, a trusted-manifest directory split) without
a fresh, unanimous research→critique cycle — a 2026-09-06 adversarial
review of exactly that sketch found it insufficient (treating membership
in an "activated" directory as authority is itself exploitable without a
protected trust grant bound to the full manifest, invocation policy,
executable identity, and publisher evidence). Start from inert
scan-and-display only, per the final security review's own verdict.

## 5. Process discipline for the rest of this epic

Restated briefly here since this doc is meant to be the one place a peer or
a future session can read to understand how to work on this; full detail in
the assistant's own memory (`project_engram_peerhub_full_separation_2026_09_02.md`,
outside this repo) is not reproduced here since it isn't repo-portable.

- **No implementation until a gate's design is unanimously ratified**
  (research → independent critique → reconciliation, as demonstrated for
  gate 2 today). A single peer's proposal is never sufficient on its own.
- **Every claim needs a real file/line/commit citation**, independently
  re-verified by whoever reads it next — never trust a peer's self-report
  of "I wrote/verified X" without checking directly (a real finding today:
  a peer's own sandboxed "writable scratch area" does not reliably persist
  to the real host filesystem after its process exits).
- **`P:\` (`stable/hub-py-restored`) is never touched.** All Engram-`main`
  work happens in the isolated worktree at
  `D:\Engram&Peerhub\engram-main-worktree`.
- **Delegate real, complete units of work to peers**; the terminal directs,
  verifies, and synthesizes rather than implementing directly. Quota policy
  (not reproduced in full here): keep `ag`/`cx` usage above the terminal's
  own, use pools aggressively without an artificial ceiling, push toward
  full exhaustion as a 7-day reset approaches.
- **Document before proceeding, and before any context reset.** Every
  research/critique/reconciliation round from today lives in
  `D:\Engram&Peerhub\engram-main-worktree\_sys\data\sessions\` (Engram-side
  research log) or `P:\workspace\peerhub\docs\design\` (this doc, and any
  future PeerHub-side design work) — never only in chat history.

## 6. Supporting documents produced today (read for full detail)

All in `D:\Engram&Peerhub\engram-main-worktree\_sys\data\sessions\` unless
noted:

1. `2026-09-02_separation-gap-analysis.md` — the four-question gap analysis
   (stable-vs-main drift, PeerHub capability audit, full Engram AI-CLI
   inventory, doc/config staleness). The source for gates 1, 3(audit part),
   5, 6, 7's raw material.
2. `2026-09-02_separation-gap-analysis-critique.md` — terminal critique of
   the above; citations spot-verified, one minor correction, one nuance
   clarified, one apparent tension resolved by today's explicit user
   directive.
3. `2026-09-02_gate2-autodetect-design-proposal.md` — first design pass for
   gate 2 (ag.deepthink). Superseded by the critique below where they
   disagree.
4. `2026-09-02_gate2-autodetect-critique.md` — independent critique (cx)
   finding the proposal not ready for ratification; found the existing
   ratified-but-dormant Phase 1 manifest/admission docs neither prior round
   had located.
5. `2026-09-02_gate2-phase1-reconciliation.md` — verification (ag) that the
   Phase 1 docs resolve half of the critique's findings and narrows gate
   2's true remaining scope to the discovery-sweep design.
6. This document.

Also directly relevant, already in `P:\workspace\peerhub\docs\design\`
(PeerHub's own repo, not the Engram worktree):

- `PHASE1-AUTODETECT-SIDECAR-2026-08-19.md` / `-V2-2026-08-20.md` — earlier
  drafts, superseded in relevant part by `PHASE1-MANIFEST-SCHEMA-V2`.
- `PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md` — **the design to reuse** for
  gate 2's contract-mapping half.
- `PHASE1-PROMOTION-SCHEMA-V1-2026-08-20.md`, `PHASE1-ARCHITECTURE-CONSOLIDATION-2026-08-21.md`
  — designate the above normative and place it behind real dispatch
  admission (both ratified, both unimplemented).
- `PHASE1-THIRDPARTY-DEFERRAL-AND-SHIMS-2026-08-20.md` — explains exactly
  why Phase 1 never attempted the discovery sweep (deliberate deferral, not
  an oversight).

## 7. Explicitly not started

Everything past gate 2's next design round: gates 1/4/5's formalization
passes, gate 6's sequencing, gate 7 (blocked on the code boundary landing),
and all actual implementation/TDD work for any gate. No code has changed in
either repository as part of this separation effort today — every artifact
listed above is a `.md` document.
