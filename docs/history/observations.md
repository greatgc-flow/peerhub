# Historical lesson observations

These records preserve the measurements and conditions behind the [operational lessons](../lessons.md).

## 2026-11-01 ag bulk profile choice

In a small Windows test with one run per cell, flash-low broke down somewhere between 25 and 150 items. Flash-medium finished the work, and high effort only added time. The test had no ground truth for images. The item counts are specific to that run. [Bulk profile lesson](../lessons.md#ag-profile-choice-for-bulk-work-ag-bulk-profile-heuristics)

## 2026-11-10 cx stream reuse

One cx stream used 23.6M input tokens over 19 asks. [Session log](work-log-2026-11-10.md#5-quota-diet), [stream reuse lesson](../lessons.md#stream-reuse-cost-reuse-within-one-task-only-stream-reuse-cost)

## 2026-11-12 cc parent shell lifetime

On a 32 GB Windows host running PeerHub 0.24.8, memory pressure reaped parent shells and killed long cc asks when about six lanes were active and one loaded a large index (#352). [Concurrent cc lanes lesson](../lessons.md#concurrent-cc-lanes-and-parent-shell-lifetime-concurrent-cc-lanes)

## 2026-11-13 peer sub-agents

The check for #361 covered `Agent` in cc, `spawn_agent` in cx, and `invoke_subagent` in ag. The cx check used Codex 0.167.2 with `features.multi_agent` enabled and named `spawn_agent` explicitly in the prompt. [Peer sub-agents lesson](../lessons.md#peer-sub-agents-inside-one-ask-peer-subagents)

## 2026-11-14 independent peer votes

Three peers answered in 11 s in the measured run. [Independent review votes lesson](../lessons.md#independent-review-votes-independent-peer-votes)
