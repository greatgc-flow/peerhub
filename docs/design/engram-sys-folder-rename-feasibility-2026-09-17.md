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

## Addendum (2026-09-18): a smaller, distinct, worthwhile step — 2-voice converged

Does not reopen dynamic renameability. A narrower, low-risk, incremental hygiene improvement is worth doing on its own merits, verified separately from the rejected big-bang approach:

**Centralize the 31 files' own `Path(__file__).resolve().parent.parent` (or equivalent) self-location logic into one shared module** (e.g. `_sys/core/root.py`, computing it exactly once) that those files import and call instead of re-deriving it themselves. This is a pure DRY refactor — zero behavior change, each file convertible and test-verified independently, one at a time. It does **not** make `_sys` renameable: the shared module itself still lives inside a folder literally named `_sys`, and the 14 files with `from _sys.<module> import ...` still have a hard Python import-namespace dependency on that exact name — this is a language-level constraint (Python's import resolution requires a real matching package/folder name), not something a shared constant or environment variable can abstract away. Those 14 files are the genuine boundary of renameability and should be left untouched; a thin `sys.path` shim was considered and rejected (breaks static analysis, IDE tooling, and PEP 8 for no real gain). Centralizing the other 31 is still valuable independent of renaming: it shrinks the actual surface area of path-hardcoding, and is the specific prerequisite that would make a future one-time migration tool meaningfully simpler to build (updating one shared module's self-location call plus the 14 import sites, not 31+ independent call sites).

**Proposed migration order, safest first, each phase fully verified before the next:**
1. **Unit tests** (`_sys/tests/unit/test_*.py`, ~29 files) — fail loudly in CI/dev only, no production exposure.
2. **Low-risk tooling/checks** (`_sys/checks/_common.py`, `_sys/checks/check_*.py`, `tools/winget/build_*.py`, `import_legacy_manifest.py`) — standalone, outside the critical runtime path.
3. **Core non-critical modules** (`_sys/core/version_resolver.py`, `tidy_temp.py`, `migrate_ais_to_engram.py`, `dispatcher.py`). Note: `_sys/core/version.py` uses `Path(__file__).parent` (one level shallower than the rest, verified by direct read) — do not blindly apply the same `.parent.parent` substitution here; check each call site's actual depth before converting.
4. **HIGH RISK, last, extra care** (`_sys/core/provisioner.py`, `setup.py`, `updater.py`, `uninstaller.py`, `_sys/tests/unit/conftest.py`) — boot/update/uninstall-critical; a mistake here can leave an install unable to start or self-update, or break the entire test suite's fixture setup.

Not yet implemented — a proposal for cx's review (still rate-limited) alongside the rest of this backup-simplification round.
