# ag.effort DIR-006 ratification: `_sys` folder rename feasibility (2026-09-19)

**Role:** `ag.effort`, standing in for `cx` as a one-time, user-authorized substitute vote (DIR-006 exception, not a policy change).

**Target document:** `docs/design/engram-sys-folder-rename-feasibility-2026-09-17.md` (main body + Addenda 1-3)

**Verdict: RATIFY WITH AMENDMENT.** Full amendment text (Addendum 4) applied to the target document.

---

## Discarded first attempt: a false REJECT from wrong-repo-root confusion

The first dispatch of this task auto-profiled to `ag.deepthink` (hub tier auto-selection overrode the requested `ag.effort` tier based on prompt-content scoring) and returned **REJECT**, claiming the design document analyzed a "hallucinated" codebase: it asserted `engram.cmd`, `_sys/core/setup.py`, `updater.py`, `uninstaller.py`, `provisioner.py`, `version.py`, and `layout_migration.py` do not exist, and that the file counts (31 self-location files, 9 import files) were wildly wrong (claimed actual: 414 files).

cc independently checked every one of these claims directly against `D:\Engram&Peerhub\engram-main-worktree` (the correct Engram product source root) and found **every claimed-missing file exists exactly as the original design document said**. The likely root cause: the dispatch prompt's context header said "Repo root: P:\", and `ag.deepthink` most likely searched `P:\_sys` -- a completely different, older "frozen legacy" AI-collaboration-hub codebase that coincidentally also has a `_sys/core/` folder, but with unrelated contents (`hub.py`, `quota.py`, `pathlayout.py`, no `layout_migration.py`, no `version.py`, `.bat` files instead of `engram.cmd`). This was confirmed by directly listing `P:\_sys\core\` and finding it matched several of the "missing" claims exactly.

This REJECT was discarded as invalid and not applied to the document. The task was redispatched with: (1) an explicit, unambiguous repo-root instruction (`D:\Engram&Peerhub\engram-main-worktree` for every `_sys`-relative path), (2) an explicit warning about the prior dispatch's wrong-root mistake, (3) `--to ag.effort` passed directly (rather than `--to ag --effort effort`, which only contributes to auto-scoring and does not pin the tier).

## Second dispatch: RATIFY WITH AMENDMENT, all citations independently verified

`ag.effort`'s redo report explicitly stated and used the correct repo root, ran real empirical Python tests (not reasoning-only) confirming Addendum 2's `ModuleSpec`-based import-namespace bootstrap mechanism, and found several genuine (not wrong-root) citation corrections against the actual current files:

- Self-location file count: **65 files** (not 31) perform `Path(__file__)` self-location logic.
- Import count: **24 import lines across 12 distinct files** (not 14 across 9) use `from _sys.<module> import ...` / `import _sys.<module>` -- missed `_sys/checks/check_encoding.py:41`, `tools/winget/build_package.py:53-54`, and `test_dispatch_wiring.py:202`, `test_launcher_log.py` (different import style: `import _sys.core.launcher as launcher`).
- One additional bootstrap-injection call site needed: `_sys/checks/check_encoding.py` (has its own `__main__` entrypoint).
- Packaging-script line numbers drifted since 2026-09-18 (`build_package.py` and `import_legacy_manifest.py`, several lines each) -- current numbers documented in Addendum 4.
- Migration-order qualification: `conftest.py`'s bootstrap call should move from Addendum 1's Phase 4 to Phase 1.

**Every one of these citations was independently re-read by cc against the real files at `D:\Engram&Peerhub\engram-main-worktree` before acceptance** -- all confirmed accurate, including a direct grep cross-check of the "12 distinct files" import count that matched exactly once the different import styles (`from _sys.X import` vs `import _sys.X`) were accounted for.

## Final verdict

**RATIFY WITH AMENDMENT.** The Addendum 2 technical thesis (PEP 420 `ModuleSpec` namespace-package bootstrap) is validated by two independent empirical test rounds (cc's original test, ag.effort's redo). Addendum 1's phased safety approach and Addendum 3's packaging-scope distinction are sound. Ratification is conditioned on incorporating the corrected file/import counts, adding the one missed bootstrap call site, and updating packaging-script line numbers -- all applied as Addendum 4 in the target document. **Implementation of Part 1 (bootstrap + `_sys/core/root.py` + Phase 1 unit-test migration) may now begin.** Phase 4 (HIGH RISK: `provisioner.py`, `setup.py`, `updater.py`, `uninstaller.py`) remains explicitly gated on a full green test suite across Phases 1-3 and a batch-entrypoint (`engram.cmd`) discovery design that has not yet been written.

## Why this matters for future dispatches

This is the first time in this session a peer's REJECT verdict was itself found to be built on a verifiably wrong premise (wrong repo root), not just an imprecise citation. Two lessons for future substitute-vote dispatches: (1) always give an explicit, unambiguous absolute repo-root path for every relative path referenced in a dispatch prompt, especially when the project has multiple superficially-similar folder trees (`P:\_sys` vs. `D:\Engram&Peerhub\engram-main-worktree\_sys`); (2) `hub.py ask --to ag --effort effort` does not reliably pin the `ag.effort` tier -- it only contributes a scoring signal that can be outweighed by prompt-content markers, so use `--to ag.effort` directly when the specific tier matters.
