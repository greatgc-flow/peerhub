# Batch 08f — test_stage2_boundary.py Peer & Role Registry Preservation Record

> LegacyTranslator retirement, sub-batch 8f of 8 (overall batch 8 of 9).
> Baseline commit: `d463770` (sub-batch 8e).
> Target: partial rewrite of `tests/integration/test_stage2_boundary.py` (6 peer_and_role_registry test nodes off `LegacyTranslator`).
> Gate result: PASS, 0 failures (`tools/legacy_retirement/runs/batch-08f/report.md`).

## Summary

Rewrote the 6 `peer_and_role_registry` test nodes in
`tests/integration/test_stage2_boundary.py` off `LegacyTranslator`,
constructing typed `Command` objects directly and wrapping each in
`SimpleNamespace(command=...)` where the original assertion text needed
to survive unchanged.

The 6 rewritten test functions are:
1. `test_legacy_list_nodes_translates_and_executes` -> constructs `ListNodesCommand` directly
2. `test_legacy_register_node_translates_and_executes` -> constructs `RegisterNodeCommand` and a follow-up `ListNodesCommand` directly
3. `test_native_bind_profile_and_legacy_model_status_execute` -> its native `BindProfileCommand` half was already untouched; its legacy half now constructs `ModelStatusCommand` directly
4. `test_legacy_assign_role_and_role_status_translate_and_execute` -> constructs `AssignRoleCommand` and a follow-up `RoleStatusCommand` directly
5. `test_legacy_release_role_translates_and_executes` -> constructs `ReleaseRoleCommand` directly
6. `test_legacy_release_unassigned_role_is_a_successful_noop` -> constructs `ReleaseRoleCommand` directly (unassigned-role no-op case)

All other 55 test functions (76 parameterized invocations) in
`test_stage2_boundary.py`, including the 28 already-rewritten 8a-8e
nodes, were **completely untouched** and verified with zero assertion
delta.

## Per-Node Assertion Table (Touched Nodes)

| Test Node ID | Before | After | Delta | Notes |
|---|:---:|:---:|:---:|---|
| `test_legacy_list_nodes_translates_and_executes` | 4 | 2 | -2 | Waived 2 translator-shape checks (`TranslatedCommand`, `ListNodesCommand`) |
| `test_legacy_register_node_translates_and_executes` | 7 | 4 | -3 | Waived 3 translator-shape checks (`TranslatedCommand` x2, `RegisterNodeCommand`) |
| `test_native_bind_profile_and_legacy_model_status_execute` | 8 | 6 | -2 | Waived 2 translator-shape checks (`TranslatedCommand`, `ModelStatusCommand`); the native `BindProfileCommand` half of this test is untouched |
| `test_legacy_assign_role_and_role_status_translate_and_execute` | 10 | 6 | -4 | Waived 4 translator-shape checks (`TranslatedCommand` x2, `AssignRoleCommand`, `RoleStatusCommand`) |
| `test_legacy_release_role_translates_and_executes` | 5 | 3 | -2 | Waived 2 translator-shape checks (`TranslatedCommand`, `ReleaseRoleCommand`) |
| `test_legacy_release_unassigned_role_is_a_successful_noop` | 4 | 3 | -1 | Waived 1 translator-shape check (`TranslatedCommand`) |
| **Touched Total** | **38** | **24** | **-14** | 14 translator-shape assertions retired (all waived); every real outcome/state assertion preserved byte-identical |

## Real Assertions Preserved (Not Retired)

- `test_legacy_list_nodes_translates_and_executes`: `outcome` success check and the `{node_id}` set-equality check over `outcome.result["nodes"]`.
- `test_legacy_register_node_translates_and_executes`: `outcome` success check, `target_id == "peer-node:legacy-worker-1"`, and the follow-up `list-nodes` submission's `any(...)` membership check.
- `test_native_bind_profile_and_legacy_model_status_execute`: `bound.result["target_id"]` check (native, untouched), `outcome` (model-status) result assertions.
- `test_legacy_assign_role_and_role_status_translate_and_execute`: `translated.command.peer_node_id == "cc"`, `outcome` success check, `target_id == "role-assignment:implementer"`, and the follow-up `role-status` submission's role-list assertion.
- `test_legacy_release_role_translates_and_executes`: `outcome` success check and `outcome.result["disposition"] == "RELEASED"`.
- `test_legacy_release_unassigned_role_is_a_successful_noop`: `outcome` success check and the exact `outcome.result` dict equality (no-op disposition).

## Retired Assertions (Translator-Only Waivers)

See `docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch08f.json`
for the full waiver list (14 entries total). Every retired assertion is a
translator-shape check: `isinstance(translated, TranslatedCommand)` or
`isinstance(translated.command, Type)`.

## Untouched Nodes Confirmation

All 55 untouched test functions (76 parameterized invocations, including
the 28 sub-batch 8a-8e nodes) in `tests/integration/test_stage2_boundary.py`
were verified by the gate to have **exactly 0 assertion delta**, confirmed
via the per-node table in `tools/legacy_retirement/runs/batch-08f/report.md`.

## Gate Extensions

No modifications to `tools/legacy_retirement/gate.py` were required for
this sub-batch; all retired assertion shapes matched existing verified
`translator_only()` patterns.

## Coverage Delta

Zero lost lines, zero lost branches, across all production modules touched
(confirmed by the gate's coverage comparison; the gate reported PASS).

## Notes on This Sub-Batch's Dispatch

The rewrite and waivers file survived a background dispatch that was
killed by a host memory constraint after the gate had already run and
reported `Result: PASS` (evidenced by the surviving
`tools/legacy_retirement/runs/batch-08f/` snapshot), but before the doc/
commit/push steps ran. The terminal reviewed the full diff for quality
(no discarded-value or gutted-assertion regressions found; every real
assertion listed above is intact), independently re-ran the gate against
the surviving tree state (fresh `PASS: 0 failure(s)`), wrote this
document, and ran full suite/pyright/commit itself.

## Gate Command

```bash
python tools/legacy_retirement/gate.py \
    --baseline main \
    --output tools/legacy_retirement/runs/batch-08f \
    --waivers docs/reviews/legacy-translator-retirement/gate-canary-waivers-batch08f.json \
    tests/integration/test_stage2_boundary.py
```
