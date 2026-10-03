# M1 open questions (catalog ambiguities; oracles never weakened)

- Q-W0-1 (META-001..004): spec package was already validated, so these were GREEN on first run (no RED phase). They guard future catalog edits only.
- Q-W0-2 (SCH-011/012/013): "API persistence through a transaction fixture" is only defined for Peer/Stream in the harness (`persist_wire`);
  Record/Offset mutants are checked at `parse_wire` (no wire-level persistence port yet; Record append goes through `append_record`). Revisit in Wave 1.
- Q-W0-3 (SCH-007/008): Observation / Resource Pool have no Core model (extension-owned); validated schema-only.
- Q-W0-4 (ARCH-001): "Core package entrypoints" interpreted as every module under `peerhub/m1/`; the CLI composition root (imports Diag) was moved to `peerhub/m1_cli.py`.
- Q-W0-5 (ARCH-004): ReadonlyDiag shares a module with ObservationStore (writer); the rule is applied to the `ReadonlyDiag` class (ctor deps, names, SQL verbs, `mode=ro`) plus module imports. Splitting the module is advisable in Wave 5.
- Q-W0-6 (SCH-005/PROP-008): digest covers `kind + body` only (metadata/targets excluded); TD-20/TD-idempotency scope to be confirmed in Wave 1 (IDEM).
