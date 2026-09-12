# PeerHub UX / Structure Simplification — Proposal B

**Status:** PROPOSED
**Date:** 2026-09-12
**Scope:** PeerHub user-facing surface (CLI ergonomics, install experience, config layout, root hygiene, README). Core architecture is explicitly out of scope.

## 0. Context and Methodology

This proposal is an independent (Voice B) review of PeerHub's UX and structure, focusing on the day-to-day experience of a human or AI using the tool. 

**Relationship to the 2026-09-07 finding:** 
The 2026-09-07 project memory finding ("core `ask` sound but buried in enterprise-CQRS ceremony; proposals/consensus redundant") is **validated and extended** by this proposal. The finding accurately diagnoses the symptom (a flat, overwhelming CLI namespace), but lacked a concrete remedy. This proposal provides the empirical measurements and the specific simplification strategy to resolve it.

All findings below are based on empirical verification against the repository at `P:\workspace\peerhub` on 2026-09-12.

---

## 1. Facts Found (Empirical Evidence)

1. **CLI Command Surface is Overwhelming:** Running `peerhub --help` yields exactly 30 top-level commands in a single flat namespace (e.g., `workspace`, `status`, `ask`, `broadcast`, `node`, `lock`, `artifact`, `role`, `routing`, `leadership`, `feedback`, `error`, `alert`, `room`, `duty`, `session`). 26 of these are internal plumbing or governance API endpoints.
2. **High Friction in `ask`:** The `peerhub ask --help` output reveals that `--capability-tier` is a mandatory argument. A user cannot simply run `peerhub ask ag "hello"`; they must specify `peerhub ask ag "hello" --capability-tier READ_ONLY`. Furthermore, `--workspace` defaults to `.`, which risks accidentally initializing a SQLite database in whatever random directory the user happens to be in.
3. **Config/Dotdir Layout is Solid:** The recent config consolidation documented in `docs/config-hierarchy.md` and `docs/design/dotdir-consolidation-RATIFIED-2026-09-09.md` successfully moved config into `~/.peerhub/config/` and `<workspace>/.peerhub/config/`. The commands `validate`, `init`, and `migrate` exist and function well.
4. **Root Folder Hygiene is Cluttered:** A `list_dir` of the repository root shows `.ai/`, `scratch/`, `.hypothesis/`, and `.pytest_cache/`. While `.gitignore` properly ignores these (preventing them from shipping to PyPI), `scratch/` contains ad-hoc test scripts (`patch_arbiter.py`, `pytest-item1-a/`) that add visual noise for anyone cloning the repo.
5. **README is an Architecture Ledger:** The `README.md` is ~21KB (173 lines) and heavily front-loaded with historical development logs (e.g., "hub.py-replacement TDD", "LegacyTranslator fully retired", and deep dives into the MECE audit). The actual "Install" and "Try it" sections are buried below 66 lines of dense architectural history.

---

## 2. Proposed Changes

### 2.1. CLI Command Surface (Solving the "CQRS Ceremony")
**Finding:** 30 top-level commands force the user to visually parse governance infrastructure to find the 3-4 commands they actually need (`ask`, `status`, `diag`).
**Proposal:** 
Introduce a hierarchical CLI namespace. 
- **Primary Commands:** Keep only `ask`, `status`, `diag`, `config`, `workspace`, and `broadcast` at the top level.
- **Governance/Internal Commands:** Move the long tail (`lesson`, `routing`, `leadership`, `lock`, `duty`, `session`, etc.) under a secondary namespace, such as `peerhub gov <command>` or `peerhub internal <command>`.
- **Impact:** `peerhub --help` shrinks from 30 commands to 6, drastically improving discoverability.

### 2.2. Install / First-Use Experience
**Finding:** Mandatory flags on the primary verb (`ask`) create unnecessary typing friction.
**Proposal:**
- Make `--capability-tier` optional in `peerhub ask`. It should default to `READ_ONLY` (safe by default) or be read from the workspace's `ask.toml`.
- Remove implicit workspace initialization. If `peerhub ask` is run in a directory without a `.peerhub/` folder, it should operate ephemerally or prompt the user, rather than silently creating `peerhub.sqlite3`. (This aligns with the recent task 2 from the dotdir ratification).

### 2.3. Config/Dotdir Layout
**Finding:** The config tiering (`global` vs `workspace`) and commands are already in excellent shape.
**Proposal:** **Leave as-is.** The recent consolidation work completely addressed the configuration sprawl. No further changes are needed in this round.

### 2.4. Root Folder Hygiene
**Finding:** `scratch/` and `.ai/` are artifacts of the local AI agent environment, not the project itself.
**Proposal:** 
- Add a cleanup script or git hook to automatically purge `scratch/` periodically, or relocate ad-hoc agent scratchpads into `.ai/scratch/` so only a single non-tracked folder exists at the root.
- Keep the `.gitignore` rules as they are, but document the presence of these folders in a `CONTRIBUTING.md` so new contributors aren't confused.

### 2.5. README Simplification
**Finding:** The README functions as a project history ledger, making the critical path ("How do I use this?") hard to reach.
**Proposal:**
- **Extract History:** Move lines 5-66 (the "Status" and historical audit paragraphs) into a new file: `docs/design/PROJECT-HISTORY.md`.
- **Focus README:** Reduce the README to exactly four sections: **What is PeerHub**, **Install**, **Try it**, and **Architecture/Design Docs** (providing a link to `docs/design/`).

---

## 3. Risks and Tradeoffs

- **CLI Breakage:** Moving 24 commands under a `gov` namespace will break any existing scripts calling them directly. 
  - *Mitigation:* Retain the top-level commands as hidden, deprecated aliases that emit a warning for one minor version cycle before removal.
- **Default Capability Tier:** Defaulting to `READ_ONLY` might confuse users who expect agents to be able to edit files immediately.
  - *Mitigation:* Ensure the `ask` output clearly states `(Tier: READ_ONLY)` when a mutation is blocked, with a hint to use `--capability-tier WORKTREE_WRITE`.

## 4. Implementation Estimate

- **CLI Hierarchy:** ~2 hours (re-registering subcommands into a nested structure).
- **`ask` Defaults & Initialization Guards:** ~1 hour.
- **README / Docs Refactoring:** ~30 minutes.
- **Total Scale:** ~Half a day of work for a single agent. No core state machine or database schema changes are required.
