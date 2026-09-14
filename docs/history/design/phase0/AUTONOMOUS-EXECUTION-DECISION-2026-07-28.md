# Autonomous Phase 0 Execution Decision — 2026-07-28

AG effort and CX effort independently reviewed the remaining Phase 0 backlog.
Both agree that legacy regressions are supporting evidence only and that
PeerHub source TDD remains gated by the Phase 0 artifact set and ratification.

## Resolved operating choices

1. Continue evidence, inventory, and TDD-contract documentation autonomously;
   do not wait for per-fixture approval.
2. Do not begin PeerHub source implementation before the stated evidence/design
   gate is ratified.
3. For an unavailable quota-consuming recovery allowance, use the safe
   `RECOVERY_DEFERRED` outcome.  No human allocation decision is required until
   production recovery canaries are to be enabled; terminal/premium reserves
   are never borrowed implicitly.
4. Complete the 90-action fixture-policy linkage as a document/inventory task,
   then surface only genuine unmappable actions for a decision.
5. Preserve CC/Fable for final ratification after AG/CX evidence is complete.

## Consequence

The next autonomous work is: finish individual evidence records where legacy
observation exists; specify V1-only fake cases without falsely marking them
captured; complete fixture/action inventory validation; fold the resulting
contracts into TDD-ready documents; then request final ratification.
