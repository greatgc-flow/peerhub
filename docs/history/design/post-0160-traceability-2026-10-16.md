# Post-v0.16.0 Traceability and Recursive MECE Ledger (2026-10-16)

**Ledger Pins (Rule: Invalid if any pin changes):**
- **Baseline v0.16.0:** `5951d0f2a32aa85151f6733f286eea03281fec55`
- **Checkout HEAD:** `c717ee0097c7a1d89fa8d72616207d0dd0db7588` (on `fix/monitor-detail-expansion`)
- **Upstream origin/main:** `d392fe4c4f63b5e9f4b6629c69d12cbe056d1bfc` (PR #110 REL-005 oracle, beyond checkout)
- **Pinned UTC Time:** `2026-10-16T08:52:57Z`
- **Design Digests:** `monitor-expand-details`=`A1BA27EDFEA28EFA967D09A2DDCE105249DAFE27AD116F4B7080A35790A24CDA`, `monitor-e2e-lineage`=`10F04DC69312D4AAEC4203CAC1761A27D9DBA50CB416A5506C6F0AB510244047`, `live-evidence-archive`=`5836FE148FD92373BD6FC1B5179638B5796288C7C455260E93FA89B1882923A0`

---

## Axis 1: 50-Commit Partition (v0.16.0..HEAD) & Dispositions

| Cluster | Ordinals (`git log --reverse`) | Key Commits (First..Last) | User-Visible Behavior | Design Section | Gap? | Disposition | Risk |
|---|---|---|---|---|---|---|---|
| **C1: Telemetry Receipts** | 17–19, 21–22 (5) | `6fd3daff`..`3d59c6ff` | Bounded AG parser; schema-versioned receipts | `monitor-e2e-lineage` § Core Lineage | No | REVISE | Low |
| **C2: Session Latency/Coverage** | 27–31 (5) | `23e6761d`..`d5d839e2` | Process-scoped timing, 20-scope daily latency | `monitor-e2e-lineage` § Coverage | No | KEEP | High (DB downgrade hazard >=v0.16.1 via `d5d839e2ec1079d73c4d10dbb99412048f8dd9d1`) |
| **C3: Read Model & Quota** | 34–37 (4) | `24cde3e9`..`494857a2` | Grouped SQLite reads, quota pace calculation | `monitor-e2e-lineage` § Lineage/Urgency | GAP-1 | REVISE | Med (omitted reset_credit) |
| **C4: Grouped Default View** | 43–46 (4) | `841c2d38`..`dc510f4f` | Breaking: default `monitor` is grouped view | `monitor-expand-details` § Scope | GAP-2 | REVISE / OPEN-1 | High (owner rollback) |
| **C5: Dashboard Renderer** | 47 (1) | `5421765f` | Bounded TTY planner; 2-record detail cap | `monitor-expand-details` § Layout | GAP-3 | REVISE | Med (starvation / budget) |
| **C6: CI Restructure** | 26 (1) | `8293352a` (cross-tag `5d2224ec`) | Streamline platform matrix & aggregate status | CI workflow (non-design) | No | KEEP | Low |
| **C7: Release & Evidence Tooling** | 16, 38–40, 48 (5) | `70c958d2`..`75677b07` | Dirty-tree canary checks; v0.16.1 & v0.18.1 prep | `live-evidence-archive` (deferred) | GAP-4 | REVISE | Med (stray drafts/archive) |
| **C8: Docs & Research** | 1–15, 20, 23–25, 32–33, 41–42, 49–50 (25) | `7a3a4b96`..`c717ee00` | Review packets, telemetry audits, handoff sync | `HANDOFF.md` § 0 | No | REVISE | Low (status/digest drift) |
| **C9: Beyond Checkout** | N/A (PR #110) | `d392fe4c` (on `origin/main`) | REL-005 pytest oracle hardening | Standalone test oracle | No | KEEP | Low (orthogonal to monitor) |

---

## Axis 2: Owner Requirements Ledger (PLANNED vs MEASURED)

| Owner Requirement | Design Response & Evidence | Status |
|---|---|---|
| Collaborate with all peers (CC, CX, AG) | Active participation across rounds 1–8; approval pending unanimous review | MEASURED (debate active; round 7 BLOCK) |
| 7D pace balance (`cc <= ag == cx`) | Slicing: PR1 (CX cx.effort), PR2 (AG ag.effort), PR3 (CX/AG), review (CC cc.effort) | PLANNED (for PR series) |
| Terse human terminal output | Min-width 32 cutoff, 2-line capacity headline, compact fitting priority | PLANNED |
| Small reviewable edits (TDD slices) | 3 staged PRs with RED test prerequisites; archive separated | PLANNED |
| Design perfect before implementation | Unanimous approval on exact SHA + document digests required prior to code | MEASURED (enforced across rounds 1–8) |
| Uncompromising unlimited-round debate | Round 7 ended in BLOCK review; round 8 addressing open audit items | MEASURED |
| English for AI / source / durable docs | English used across all designs and source docs | MEASURED |
| Handoff file + single-line prompt | `docs/HANDOFF.md` updated with checkpoint and 11 decisions | MEASURED |
| Escalate and re-open prior design/impl | All post-v0.16.0 changes audited; rollback explicitly evaluated for C4 | MEASURED |
| Agree model/effort/process up front | Model/effort explicitly assigned per PR slice | PLANNED |
| Tidy GitHub branches/releases/worktrees | GitHub audit: PRs 96–110 merged; cleanup of remote heads and worktrees mapped | PLANNED (OPEN-3) |
| Clear adaptive monitor, all data reachable | Bounded fairness detail rows; inspection mode prints all loaded; DB history cited | PLANNED |
| Normal refreshes, `--snapshot` read-only | Normal triggers provider probes; `--snapshot` is pure read model | MEASURED (existing CLI behavior) |
| v0.16.0 trusted baseline alignment | CLI rolled back to 0.16.0 by owner; reconcile breaking changes vs compatibility | OPEN-1 (owner decision) |

---

## Axis 3: Repo Hygiene & GitHub State

- **GitHub PR Evidence (verified via `gh` at 2026-10-16T08:52:57Z):** PRs 96–110 all MERGED into `origin/main` (squash-merged; do not rely on `--is-ancestor`).
- **Release Evidence (verified via `gh` at 2026-10-16T08:52:57Z):** `v0.18.1` Draft (tag points to `75677b07`); `v0.18.0` Latest; `v0.17.0` Draft; `v0.16.1` published; `v0.16.0` published.
- **Remote Heads (`git branch -r`):** Active: `origin/main`, `origin/fix/monitor-detail-expansion`. Merged/prune candidates: `origin/release/0.16.1`, `origin/feat/peerhub-monitoring-20261015`, `origin/docs/release-evidence-0.16.1`. Historical: `origin/legacy/v0-main-final`.
- **Local Worktrees:** `peerhub` (main), `peerhub-live-0161`, `peerhub-release-0161`, `peerhub-v0160` (dedicated comms worktree).

---

## Axis 4: Contract Changes, Migration Hazards & Drift

| Dimension | Post-v0.16.0 Change | Impact & Invariants | Action |
|---|---|---|---|
| **CLI Contract** | Default bare `monitor` switched to grouped view | `--legacy` is deprecated (removed in 0.19.0) and warns while rendering grouped view (`monitor.py:115-121`); flat human view not restored | OPEN-1 (keep vs rollback) |
| **DB Downgrade Hazard** | C2 (`d5d839e2ec1079d73c4d10dbb99412048f8dd9d1`) added columns to `bridge_session_monitor_scope` | Any store written by >=v0.16.1 causes v0.16.0 positional 6-value INSERT (`session_receipts.py:87,104`) to fail on column count | OPEN-2 (prohibit 0.16.0 on >=0.16.1 DB) |
| **Read Model Schema** | Additive `last_known_measurements` & coverage DTO | Preserves legacy `measurements: null` for stale; `read=unknown` added | Handled in PR1 / PR2 |
| **Tests & CI Performance** | Platform matrix aggregated (`8293352a`); REL-005 oracle (`d392fe4c`) | Test execution status and runtime claims: UNVERIFIED (no tied benchmark run in ledger) | Handled in C6 / C9 |
| **Docs & STATUS Drift** | `STATUS.md` and `HANDOFF.md` reference old 0.16.1 receipt files | Updates mapped to follow archive PR series; section 0 updated | Handled in C8 / HANDOFF |

---

## Axis 5: Recursive MECE Deconstruction (GAP & OPEN Leaves)

### GAP Deconstruction
| Gap ID | Sub-dimension | Root Cause | Resolution in Current Design Set |
|---|---|---|---|
| **GAP-1** | `reset_credit` query gap | `monitor_read_model.py` queried only quota/rate_limit | Included in capacity query as dedicated unranked credit rows (PR1) |
| **GAP-2** | Breaking grouped default view | Default changed in `841c2d38`; `--legacy` only warns without flat human fallback | Reconciled via `effective_preferences()` configuration; OPEN-1 decides default |
| **GAP-3** | Capacity 2-line budget contradiction | Capacity reserved 2 summary lines, starving details | Recomputed 80x24 (11 slots) and 80x60 (47 slots) with arithmetic derivation (PR3) |
| **GAP-4** | Live evidence archive concurrency | Concurrency model disputed (OS lock vs atomic replace) | Archive deferred to separate PR series; lifecycle gaps closed |

### OPEN Decidable Leaves
| Leaf ID | Decidable Leaf Title | Options | Recommended Action |
|---|---|---|---|
| **OPEN-1** | `Default-CLI-Mode-Reconciliation` | **(a)** Grouped default with `effective_preferences()` override; **(b)** Roll back default to v0.16.0 flat view | **(a)** Retain grouped dashboard as default, honor user/workspace config overrides, document deprecated `--legacy` behavior |
| **OPEN-2** | `Database-Downgrade-Safety-Contract` | **(a)** Prohibit running v0.16.0 on stores written by >=v0.16.1 (C2 `d5d839e2`); **(b)** Write downgrade migration script | **(a)** Require dedicated comms DB (`--db`) for v0.16.0; never run v0.16.0 CLI against >=v0.16.1 store |
| **OPEN-3** | `Remote-Branch-and-Worktree-Pruning` | **(a)** Delete merged remote branches & prune extra worktrees; **(b)** Retain all | **(a)** Prune merged remote branches and release worktrees; preserve `peerhub-v0160` comms worktree |
| **OPEN-4** | `Live-Evidence-Concurrency-Model` | **(a)** OS-level byte locking (`msvcrt`/`fcntl`); **(b)** Atomic tempfile replace without locks | Settle during dedicated archive PR review; sequential canaries favor (b), xdist favors (a) |

