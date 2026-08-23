# hub.py Replacement — 7-Category Design Reinforcement Index (2026-08-24)

Triggered by: user request ("설계보강 ㄱㄱㄱ") after `HUB-REPLACEMENT-GAP-AUDIT-2026-08-23.md`
found peerhub could not replace hub.py today (only 5 CLI commands exposed
vs hub.py's 80+), and a same-day follow-up confirmed that implementing
already-existing designs would NOT be enough — no category had a ratified
peerhub-side design. This index tracks the first-round design draft for
all 7 blocking categories, produced 2026-08-24 by `cx` (delegated per
standing quota policy, `cc`/`ag` both EXH-critical this session).

**Status: first-round drafts complete for all 7 categories.** Each has
an explicit "open questions" list requiring further dialectical rounds
and/or user ratification before any implementation starts (per the
standing rule: architecture must be complete before implementation).
None of these are final specs yet — treat every category as "direction
proposed, details open" until its own open-questions list is resolved.

| # | Category | Doc | Open items |
|---|---|---|---|
| 1 | Compatibility command surface / migration strategy | `HUB-REPLACEMENT-GAP1-COMPAT-STRATEGY-2026-08-24.md` | entrypoint + session-ID mapping RESOLVED (round 2); JSON schema proposed; exit-code table + exact initial command set still explicitly data-dependent (need real static/dynamic caller-traffic measurement, not just reasoning) |
| 2 | Consensus and coordinator workflows | `HUB-REPLACEMENT-GAP2-CONSENSUS-2026-08-24.md` | 6 of 10 items RESOLVED directly against real `protocol.md §4` text (quorum function, R:5/R:8 rules, timeout duration, escalation authority, Final Call semantics) + 3 new rules found (retroactive veto, tiebreak, PTY vote-submission path); 4 items (coordinator failover, vote correction, human-override recording format, persistence/recovery) still open |
| 3 | Session/room/thread/handoff continuity | `HUB-REPLACEMENT-GAP3-SESSION-CONTINUITY-2026-08-24.md` | grounded in real `lifecycle.md`; 16 open items, mostly "does peerhub's real source already have X" questions requiring source inspection |
| 4 | Health, quarantine, leadership, role operations | `HUB-REPLACEMENT-GAP4-HEALTH-LEADERSHIP-2026-08-24.md` | 11 open items; key finding: terminal-duty (gap 3) and peer health are genuinely separate concepts; leadership/roles share gap-3's duty-lease substrate with extra policy layered on |
| 5 | Task lifecycle and failover | `HUB-REPLACEMENT-GAP5-TASK-LIFECYCLE-2026-08-24.md` | 12 open items; key finding: a "task" is a durable aggregate above request/attempt, not an alias for either; `approval-request` is a human-authorization gate distinct from gap-2's consensus |
| 6 | Governance, learning, proposal, alert commands | `HUB-REPLACEMENT-GAP6-GOVERNANCE-2026-08-24.md` | grounded in real `learning.md`/`governance.md`; 12 open items; key finding: `proposal-vote` reuses gap-2's consensus event mechanism (not a separate voting engine), directives split into user-authored vs runtime-generated layers |
| 7 | Legacy diagnostics/telemetry parity | `HUB-REPLACEMENT-GAP7-DIAGNOSTICS-2026-08-24.md` | grounded in real `diag-telemetry-architecture.md` + real `_sys/cli/diag.py`; 8 open items; key finding: this category is mostly an ADAPTER DELTA over an already-substantial existing design/implementation, not a redesign — main new work is the EXH/credit/model-status compatibility contracts |

## Cross-cutting architecture established across all 7

- **Gap-1's boundary governs everywhere**: "peerhub owns semantics; compatibility owns translation." No compat adapter reimplements domain logic.
- **Versioned JSON envelope** (`protocol_major`/`protocol_minor`/`schema_version`) is the native wire contract everywhere, proposed in gap-1, reused by every later category.
- **Append-only events + materialized projections** is the storage pattern for every stateful domain (consensus rounds, rooms/sessions/threads, node/health/duty, tasks, governance artifacts) — first established in gap-2, reused identically through gap-6.
- **Fenced-lease / fencing-token discipline** governs every form of exclusive ownership (terminal duty, leadership, roles, task-executor assignment) — one substrate (gap-3/gap-4), with domain-specific policy layered per use (gap-4's leadership challenge window, gap-5's task reassignment).
- **"Never synthesize a value you don't have"** is a repeated explicit rule: gap-4 (quota/EXH must be `UNKNOWN` not inferred), gap-6 (arbiter output is advisory unless a policy explicitly allows auto-resolution), gap-7 (diagnostics report `unknown`/`unavailable`, never healthy/zero/idle, when an upstream domain has no data).

## `cx`'s closing judgment (end of gap-7, having drafted all 7)

> "Across the seven categories, the design layer now appears substantially
> sketched for a full hub.py replacement... the remaining risk is
> specification closure and conformance rather than an unrecognized
> architectural category."

## What this does NOT mean

This is a **first-round sketch**, not a ratified specification. Every
category still has a real open-questions list (several explicitly
data-dependent — e.g. gap-1's exit-code table needs actual caller
inventory, several gap-3/4/5 items need direct peerhub-source inspection
that `cx`'s sandbox couldn't always perform this round). Per the standing
rule, implementation must not begin until each category's own open items
are resolved through further dialectical rounds — this index is the
starting map for that work, not a green light to start coding.

## Recommended next steps (not yet started)

1. Verify `cx`'s "peerhub already has X" claims against real peerhub source directly (several rounds flagged sandbox-access inconsistency — sometimes could read `_sys/docs-v2/`, never confirmed reading `peerhub/`'s actual source this session).
2. Resolve each category's open-questions list via further dialectical rounds (mirroring gap-2's pattern of checking against real ground-truth text where possible, rather than more reasoning-only rounds).
3. Do the real static/dynamic caller-traffic inventory gap-1 flagged as a prerequisite for its exit-code table and exact initial compat command set.
4. Only after the above: revisit the "architecture complete, proceed to Phase 2" decision.
