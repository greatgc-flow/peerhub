# LegacyTranslator preservation evidence

Run from the repository root with the project's Python environment (pytest and coverage must be installed):

```powershell
python tools/legacy_retirement/gate.py --help
python tools/legacy_retirement/selftest.py -v
python tools/legacy_retirement/gate.py --baseline b9031b4 --output tools/legacy_retirement/runs/batch-01 --waivers docs/reviews/legacy-translator-retirement/gate-canary-waivers.json tests/integration/application/test_lesson_inject.py
```

Use a new output directory each time. The tool never deletes snapshots or changes Git state. `runs/` is ignored; retain the directory when reviewing or reproducing a result. Copy the reviewable report into a batch preservation record when accepted.

The baseline snapshot contains the unmodified Git archive, including the original tests and production. The candidate snapshot contains current tracked and unignored files, including staged and unstaged changes, but excludes this runner's own evidence directory. It does not read the candidate from the Git index. Each run records source SHA-256 hashes and complete commands. Selected files keep their repository-relative pytest IDs, including parameter IDs, in both isolated snapshots. Each snapshot runs separate collect-only and execution processes. Existing pytest configuration, including slow/e2e deselection, remains active; the report distinguishes collected, selected and deselected nodes. Collection and setup/call/teardown execution evidence must match between revisions. Imports of production modules outside the snapshot fail the gate.

All `peerhub/**/*.py` files receive line and branch measurement with a dedicated raw coverage database. Runtime coverage configuration disables report exclusions and partial-branch annotations. The only removed coordinates are those inside the AST `LegacyTranslator` class in `peerhub/application/legacy.py`. Command classes and helpers elsewhere in that file remain measured. Coverage's version is recorded because its private analysis API supplies possible arcs; an incompatible API raises an error. Exact executed line and branch sets must be preserved per file: equal percentages or coverage gains elsewhere cannot hide a loss. Changes in production outside the excluded class or changed coverage coordinates fail closed and require explicit review and a real coordinate mapping before this gate can be used for the final production deletion.

Per-node AST assertion counts include assertions syntactically within the collected test function, including nested functions; helper/fixture assertions are not assigned to individual callers. Normalized expressions and assertion messages are retained. Assertions cannot disappear or change unless an explicit waiver identifies the exact node, expression and translator-only reason. The conservative structural validator accepts success-shape and literal command-field checks on a direct `LegacyTranslator.translate` result. It rejects calls such as `encode_params()`, arbitrary business assertions and unused waivers. Unsupported translator syntaxes fail closed. An accepted removal is recorded as an intentional retirement, never a claim that a new assertion is equivalent.

Syntax checks cannot establish universal behavioral equivalence: changed assignment values, helper behavior, fixtures and the application entry point still require review. This tool is for rewrite batches; it does not authorize the final intentional removal of translator test nodes.
