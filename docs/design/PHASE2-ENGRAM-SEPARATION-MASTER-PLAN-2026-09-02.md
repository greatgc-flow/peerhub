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
| 1 | **Ownership matrix**: Engram=environment lifecycle, PeerHub=every AI-provider/collaboration/routing/session/health/governance capability | **Substantially drafted.** The gap-analysis's §3 full inventory (every AI-touching file in Engram `main`, classified keep/move/delete) IS a first-pass ownership matrix. Needs a short explicit formalization pass, not fresh research. |
| 2 | **PeerHub autodetection design** | **Split and half-resolved.** The contract-mapping half (manifest ↔ real `PeerAdapter` protocol) is **fully solved by already-ratified-but-dormant Phase 1 docs** (`PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md`) — reuse as-is, no new design. The discovery-sweep half (actually finding installed CLIs, binding them into `registry.py` without hitting its collision guard) is the one genuinely open design gap — see §4 below for the concrete next round. |
| 3 | **PeerHub autodetection implementation + measured release**, before Engram deletes provider metadata | **Blocked on gate 2's discovery-sweep half.** Not started. |
| 4 | **Independent installation contract** (does Engram link to or install PeerHub?) | **Substantially answered as a side effect of gate 2's critique round**: no hard runtime dependency either direction — Engram reports install/package facts only, never claims PeerHub readiness; PeerHub independently discovers/probes/admits; Engram may optionally invoke PeerHub's discovery as a non-authoritative post-install hint whose failure never rolls back an Engram install. Needs to be lifted out and ratified as its own explicit decision rather than left buried in the gate-2 critique. |
| 5 | **Migration ledger** (exact facts moving to PeerHub, explicit waivers for the rest, never migrate credentials/host trust) | **First draft exists**: gap-analysis §3.13 "Move-versus-delete summary" and the full per-subsystem tables in §3.1–3.12. Needs to be formalized into its own ledger artifact (or accepted as final in its current form) — not re-researched. |
| 6 | **Engram deletion plan**, in reviewable increments, protected by a zero-AI-ownership contract | **Scoped but not sequenced.** The gap-analysis names everything to delete/narrow (§3) and notes the existing boundary test (`test_contracts.py`) already partially enforces a zero-AI-ownership contract but has 2 stale assertions (protects vendor interactive launchers Engram no longer wants — resolved by today's directive, per the gate-2 critique addendum). Needs an actual increment-by-increment sequence plan, not yet written. |
| 7 | **Packaging/doc reconciliation** (Winget metadata, root docs) after the code boundary is final | **Scoped**, gap-analysis §3.2 and §4. Genuinely blocked on the code boundary (gates 1/5/6) actually landing first — correctly sequenced last. |
| 8 | **Clean-room validation**, only in the isolated worktree + PeerHub repo, never touch frozen `stable` | **Already the working discipline** for every round done today (worktree used throughout, `P:\` status checked identical before/after every session). Ongoing process rule, not a one-time deliverable. |

## 4. What's next: the one real open design gap

Gate 2's discovery-sweep half is the critical path — gates 3, 6, and
(indirectly, since Engram's deletion of provider metadata is gated on it)
much of gate 5's actual execution wait on it. The next design round should
answer, building on cx's two-lane proposal (bounded built-in PATH
resolution for `cc`/`ag`/`cx` + trusted-manifest discovery for third
parties, from `2026-09-02_gate2-autodetect-critique.md` §7):

1. A discriminated result-type set for reporting sweep findings (not the
   flat `DetectedCLI` the first proposal used — see critique §6 for why).
2. Exactly how a discovered/admitted CLI gets bound into
   `peerhub/adapters/registry.py`'s adapter table **without** hitting its
   `register_adapter_factory()` collision guard for the already-registered
   built-in kinds (`cc`/`ag`/`cx`) — this is the one piece nothing has
   designed yet, per the Phase 1 reconciliation doc §3, row 3.
3. The trusted-manifest directory location and precedence rules (a
   provisional answer — `%LOCALAPPDATA%\PeerHub\adapters.d`, machine-local
   not workspace-scoped — was proposed in the critique, not yet
   independently re-verified by a third round).

This should go through one more research→critique cycle before
ratification, per the standing process — do not implement directly from a
single round's proposal.

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
