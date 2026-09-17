# `_sys` folder renameability — feasibility conclusion (Part C)

**Status:** Concluded, not pursued further. Research by ag.deepthink, independently re-verified by cc. Lower-priority follow-up to the backup-simplification round (`docs/design/engram-peerhub-single-folder-backup-simplification-2026-09-17.md`); user explicitly authorized skipping this if genuinely too costly.

## Finding: dynamic renameability is not viable with reasonable effort

Traced the real boot path: `engram.cmd` hardcodes `.\_sys\...` paths directly; `_sys/core/dispatch.bat` derives its own root via `%~dp0..`; `dispatcher.py` independently computes `sys_dir = Path(__file__).parent.parent.resolve()`. There is no single threaded decision point — the codebase relies on each script knowing its own position relative to a folder literally named `_sys`.

Independently verified counts (cc, direct grep against `D:\Engram&Peerhub\engram-main-worktree`):
- **31 Python files** independently re-derive their own root via `Path(__file__).resolve().parent.parent` (or the equivalent) rather than receiving it from one shared source.
- **14 Python files** use `from _sys.<module> import ...` / `import _sys.<module>` — a real Python package-namespace dependency on the literal string `_sys`, not just a path string. Renaming the folder would break these at the language level (import resolution), not just at the filesystem level.
- Batch-file entrypoints (`engram.cmd`, `_sys/core/bootstrap.bat`) hardcode `_sys\...` paths directly, with no indirection at all.

## Recommendation: do not attempt dynamic renameability

Making the live codebase name-agnostic would require, at minimum: (1) a discovery mechanism for pure batch files with no hardcoding, (2) rewriting 30+ Python scripts to stop deriving their own path and instead consume a shared, injected root, (3) resolving the Python import-namespace coupling (`_sys.core` etc.), which cannot be done by path/env-var tricks alone. This is a multi-week, high-risk restructure that reproduces the exact shape of the 2026-06-18 cancelled root-swap (`_sys` → `_sys_new`, abandoned after reaching 37,458 files with an incomplete `docs-v2` migration) — the same failure mode, not a coincidence: both are attempts to make a deeply path-coupled codebase agnostic to its own root folder's name.

**If renameability is ever a hard requirement, the only low-risk path is narrow, not dynamic:** a documented, tested, one-time migration tool — rename the folder on disk, literal-string-replace across every `.cmd`/`.bat`/`.py`/`.json` reference, rewrite the Python imports — leaving the live codebase hardcoded to whatever name the migration produced, exactly like a one-shot find-and-replace refactor rather than a runtime capability. This is a separate, bounded feature request with its own real risk (still touches every one of the ~44+ files), not something to build as a side effect of this round.

## Conclusion

Per explicit user permission to skip this if too hard: **closed as not worth attempting** in its dynamic form. No implementation follows from this round. A future narrow one-time-migration-tool request would need its own scoping and, per standing policy, its own dialectical review before implementation given the real risk profile.
