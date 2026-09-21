# Static review: Engram remediation commits (2026-09-21)

**Verdict: RELEASE-OK**

Reviewed read-only at `D:\Engram&Peerhub\engram-main-worktree`, branch
`main`, covering commits `15da09c`, `0c82044`, `d1cc169`, `282a8ad`,
`03d70a3`, and `0fd2aff`. This was a static review only; no tests were run.
The supplied audit was read. The separately named remediation-status file was
not present at the supplied peerhub path, so commit diffs and current source
were the remediation evidence.

## Findings

### Medium - undocumented `--out=PATH` compatibility changed

`_sys/checks/backup_personal_data.py:526-551` recognizes only the two-token
form `--out PATH`. `engram backup --out=archive.zip` previously returned
success (the old adapter did not recognize that spelling and therefore used
the default backup location); it now exits 2 as an unknown flag. This is an
observable compatibility change, but not a regression of the documented
grammar: README documents `backup [--out PATH]` at `README.md:62`, and the
old successful invocation did not create the requested archive path. It is
therefore non-blocking for this release. Accepting `--out=PATH` or documenting
its intentional rejection would remove the ambiguity in a later patch.

### Low - an external `tidy --sys-dir` is overwritten during planning

`_sys/core/tidy_temp.py:342-345` calls `configure_paths(root=ROOT,
sys_dir=ROOT / _SYS_DIR.name)` whenever the configured sys directory is not a
child of the configured root. Consequently, an explicit external value such
as `tidy --base-dir C:\\Engram --sys-dir D:\\Runtime` is silently changed to
`C:\\Engram\\Runtime` before cleanup planning. This does **not** affect the
normal install or the supported in-tree renamed case: for normal `_sys`, the
module initialization `configure_paths(ROOT, _SYS_DIR)` at line 138 and the
parent relationship are correct; for `ROOT\\my_runtime`, the same relationship
also holds. The `--sys-dir` help text does not state an in-root restriction,
so retain the supplied directory rather than resetting it if external sys
directories are intended to be supported.

### Low - new broad exception hides non-psutil implementation errors

The `except Exception` added at
`_sys/checks/backup_personal_data.py:176-177` catches every ordinary error in
the Codex/node process-label refinement. The fallback preserves the safe
outcome because `_is_peer_leased(...)` has already reported a running managed
process, but it can hide a programming error or malformed process object.
Narrowing it to the expected import/psutil failures would retain the fallback
without masking unrelated defects. This is not a release blocker.

## Checked items with no release defect found

- **Packaging:** `tools/winget/build_package.py:135-141` prunes the exact
  `backups` directory, and lines 198-204 exclude all `.jsonl` files during the
  actual `collect_package_files()` walk. The regression test directly invokes
  that function at `_sys/tests/unit/test_winget_manifests.py:157-182`; it is
  not a reimplementation of the filter. `git ls-files | findstr /i "jsonl
  backups"` returned no tracked matching files, while the only tracked
  `_sys/data` file is the Markdown backlog note. Thus no first-run tracked
  `.jsonl` or backup input is excluded. The new `.gitignore` entries at
  lines 87-94 cover runtime data/backups; the commit removes only the UTF-8
  BOM from line 1 and retains all prior ignore rules.
- **Normal `_sys` behavior:** backup defaults now derive from the passed sys
  directory (`backup_personal_data.py:271-274, 404-407`) and resolve to the
  same `base_dir/_sys/...` locations in an unrenamed install. The tidy module
  initialization and all derived normal paths remain `ROOT/_sys/...`.
  `check_tool_updates.py:34-35,356-362` and
  `migrate_ais_to_engram.py:146-151` also resolve to their former `_sys`
  locations when the directory has its standard name. The only intended
  normal behavior change is standalone `--backup` without `--out`, which now
  creates the documented default archive rather than rejecting it
  (`backup_personal_data.py:645-647`).
- **Dispatcher grammar:** `run_restore` still accepts `-f` and `--force`
  (`backup_personal_data.py:565-588`); `run_reset` still accepts `-y`,
  `--yes`, and `--all` (`599-613`). A quoted path with spaces remains one
  dispatcher argument and is passed to `Path` unchanged. A restore source
  beginning with `-` was already unusable before this change because the old
  parser skipped every dash-prefixed argument. README's restore/reset syntax
  and exit-code claims remain consistent with the new strict grammar.
- **Dead code:** `configure_paths` is used by module initialization, CLI
  configuration, and the dispatcher adapter; `build_plan` is used by
  `main`, not tests alone. No new test-only top-level function was found.
  The other broad `except Exception` occurrences in
  `build_package.py:474` and `check_tool_updates.py:499` predate this commit
  set's relevant edits.
