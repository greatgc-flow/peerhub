# Product Structure Cleanup and v0 Retirement
> **2026-10-08:** `core.legacy_import`, its frozen fixture and importer tests, and the `PEERHUB_CLI` selector have all been removed. Only the single `peerhub` CLI remains; v0 source is accessible only through the Git branch `legacy/v0-main-final`. The account below is a historical record.


## Decision Criteria

M1/M2/M3 are development milestones. Their names need not remain in runtime responsibilities, module boundaries, or CLI names. However, validated frozen specs, historical evidence, and durable storage/wire identifiers are preserved because their names themselves are contracts or provenance.

| Before | Now | Reason |
|---|---|---|
| `peerhub/m1/` | `peerhub/core/` | Actual responsibility for the four durable communication primitives |
| `peerhub/m2/*.py`, `peerhub/m3/*.py` | `peerhub/extensions/<capability>.py` | Isolate capabilities outside Core; do not create independent engines for each milestone |
| `peerhub/m1_cli.py` | `peerhub/cli/app.py` | Product CLI implementation |
| `peerhub-m1` console script | Only `peerhub` | Retire the temporary side-by-side entry point |
| `tests/m1`, `tests/m2`, `tests/m3` | `tests/communication`, `tests/continuity`, `tests/collaboration` | Validation scope by capability |
| `tests/unit/m1` | `tests/communication/unit` | Preserve the actual Core unit tests |
| `tools/m1_*` | `command_inventory.py`, `traceability.py`, `release_evidence.py` | Name current development tools by their roles |
| Current command inventory / implementation guide | `docs/implementation/` | Separate historical milestone evidence from current explanations |
| pytest `m1_id` marker | `catalog_id` | Preserve links to frozen requirement IDs |

## Collaboration with AG

AGY was used in plan/read-only mode for an independent review. The review conversation is `d1b9a63b-8987-412a-bd70-794617783942`. AG identified naming conflicts with the existing v0 `core`, the dynamic CLI proxy, the importer fixture's dependency on v0 persistence, data loss risks in DB discovery, package-data·workflow path updates, and preservation of frozen evidence. These findings informed the changes. The first review exceeded the time limit, so AG was asked again to stop using tools and summarize only its existing observations. AG did not modify files.

## Removed v0 Code

Removed v0 adapters / application / builtins / old core / dispatch / events / governance / health / persistence / old routing / state / telemetry, the client/runtime facade, and CLI commands/parser/context/monolith from product paths. The current Core is the new communication implementation.

Also removed v0-only unit/integration/contract/e2e/static tests, old fixture captures, and phase0 / consensus / legacy facade retirement / manifest/facts tools. The actual Core tests in `tests/unit/m1` were moved and preserved.

Deletion was carried out with a local recovery copy at `.peerhub/retired-v0-a6c6964ed6f74daf990da36388d9968a/`. The permanent Git recovery reference is `legacy/v0-main-final` / `57a137cd6a0cc7e89124ea2b87b72995087627a5`. User DBs, observations, and Records were not deleted.

## Intentionally Retained Identifiers

- `docs/m1_spec`, `docs/m2`, `docs/m3`: provenance for frozen contracts and catalogs.
- Historical CI/live/matrix evidence and wave/gate policy in `docs/m1_impl`: preserve the checksums·revisions of existing validation evidence. These are not treated as PASS evidence for this code.
- Schema `$id`, existing `m2_*` tables / `m2.*` Record kinds, the `m1` conflict field in importer JSON, etc.: compatibility contracts for persisted data·wire formats and approved plan digests. Do not rewrite them solely for naming cleanup.
- Existing `.peerhub/m1.db`: retained only as a read-only discovery fallback. New workspaces use `.peerhub/core.db`; select the nearest workspace first, and select the current name if both files exist in the same workspace. An explicit `--db` does not fall back.

## Validation Scope and Release

Updated the CLI inventory, schema package-data, import graph, current workflow paths, and test selection together. Replaced deleted legacy slow/e2e gates with new live tests for actual quota collection and public ask/retry canaries, so empty test selections cannot pass the gates. Provider tests are opt-in through `PEERHUB_LIVE=1`. Current release gating retains G0–G7 IDs·frozen requirement IDs.

Ran 1,178 tests in the local default suite: first run 1,160 PASS / 15 SKIP / 3 path-related FAIL. After fixing 2 tests that referenced previously loaded inventory paths and the last `from peerhub import m1` reference in the Core unit tests, revalidated all 3 as PASS with `pytest --lf`. Rather than presenting a single green JUnit from a full rerun, preserved the initial and revalidation results separately in `.peerhub/cleanup-junit.xml` and `.peerhub/cleanup-recheck-junit.xml`. The SKIPs were 14 other OS/Python CI cells and 1 Windows symlink permission case. The default run deselected 14 actual provider/soak tests.

Full Pyright: 0 errors; parser-derived inventory check and Wave 0–9 frozen catalog traceability: PASS. Also validated wheel/sdist builds from clean source, isolated installation, schema bytes, and Windows path/newline package tests. A failure in 1 historical evidence citation in a separate package run was resolved by specifying the record's snapshot/head scope; both items were revalidated as PASS. The installed `peerhub diag quota` read observations from the existing `.peerhub/m1.db`, and invoking a previous actual Codex request with the same ID confirmed `recovered_terminal` without rerunning the network request.

Existing release evidence is not reused to claim VERIFIED status for the new structure, and version/tag/push/publishing are not performed. Updated the editable installation of the current source to remove the temporary `peerhub-m1` script. Also moved the old incremental build cache to the local retirement copy; the new wheel contains no retired packages.
