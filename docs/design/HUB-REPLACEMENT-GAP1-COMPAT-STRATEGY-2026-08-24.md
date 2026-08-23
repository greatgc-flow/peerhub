# Gap 1 Design: Compatibility Command Surface / Migration Strategy (DRAFT — direction ratifiable, spec not yet)

Status: first-round draft from `cx`, 2026-08-24. Direction and boundary are
ratifiable; NOT yet an implementation-ready command inventory or
compatibility specification. 14 open questions listed at the end require
further dialectical rounds before this closes. Part of the 7-category
design-reinforcement effort following `HUB-REPLACEMENT-GAP-AUDIT-2026-08-23.md`.

## Recommendation: staged hybrid

1. A narrow, high-confidence hub.py-**compatible** surface for daily-collaboration-critical commands (`ask`, `ask-all`, `ask-coordinator`, `status`, `check`, `init-session`, `end-session`, `send`/`broadcast`, consensus commands, health/status commands).
2. Migrate all other callers to a native `peerhub` command surface.
3. No promise of byte-for-byte compatibility where semantics depend on hub.py's file-based state, implicit defaults, or undocumented output.

The compat layer is an explicit **adapter** with a declared compatibility matrix — not a second hub.py implementation, not a general emulation layer. Key rule: **"Peerhub owns semantics; compatibility owns translation."** No other design category should make the compat adapter authoritative for persistence, routing, leases, sessions, or consensus state.

## Compatibility contract

Adapter pipeline: `hub.py argv → validated compat command model → peerhub native request → compat response renderer`. Four responsibilities: parse/validate legacy syntax, translate to peerhub's structured request model, render legacy-format output where intentionally supported, translate peerhub outcomes to documented legacy exit-code classes. Never silently simulate unsupported behavior.

Per-command state: `COMPATIBLE` / `COMPATIBLE_WITH_LIMITS` / `MIGRATION_REQUIRED` / `UNSUPPORTED` (fails clearly with an actionable migration message, e.g. `peerhub: hub.py command 'archive-file' requires migration; see: peerhub migrate hub-command archive-file`).

## Native command surface

Versioned native CLI (e.g. `peerhub dispatch`, `session`, `consensus`, `health`, `status`, `migrate`) using structured concepts, not hub.py's historical action naming. Stable JSON output (human-readable as a presentation mode), explicit request/attempt/lease/terminal states, stable error classes, distinct capability/authorization failure outcomes, versioned schema.

## Migration mechanism

1. **Discover callers**: scripts (Python/PowerShell/.bat), `CLAUDE.md`/`AGENTS.md`/protocol docs, tests/fixtures, peer prompt templates, scheduled tasks, runbooks. Each gets a migration record (`caller_id`, `kind`, `source`, `legacy_command`, `native_command`, `owner`, `status`, `rollback_ref`).
2. **Translation tool**: `peerhub migrate hub-command --check/--explain/--render-native <command>`. Auto-rewrite only for deterministic mappings; ambiguous ones need review.
3. **Dual-run verification** for eligible read-only/safely-repeatable commands: run both paths, compare normalized stdout/stderr/exit code/timing/created IDs/visible state changes/error class/routing/ordering. Never dual-run a live mutation.
4. **Cutover gates**: documented mapping, tested expected behavior (success+failure), authorization/timeout tested, output/exit-code expectations recorded, rollback exercised, no unexplained divergence. Daily dispatch path: opt-in by caller/environment initially, not global.

## Verification strategy (4 layers)

Contract tests (adapter contract, independent of peerhub internals) → differential tests (hub.py vs peerhub on deterministic fixtures, with a declared equivalence profile per command — output/exit_code/stderr/state/allowed_variance) → scenario tests (realistic workflows: dispatch+response, peer unavailable, lease denied, timeout, retry, session reuse, consensus flow, coordinator failure, interrupted process, stale/migrated state) → caller acceptance tests (real scripts/wrappers/prompts/tests run unchanged against compat entrypoint, then against migrated native command — "CLI parity is not caller parity").

## Rollout and rollback

`PEERHUB_COMMAND_MODE`: `legacy → shadow → compat → native → retired` (compat command removed only after explicit deprecation period). Rollback = a routing change, never data-destructive: restore prior command, switch mode back, preserve peerhub request/attempt records for diagnosis, prevent duplicate execution via correlation IDs/idempotency keys, explicitly reconcile in-flight ops, never assume a partial request is safely replayable. Rollback authority scoped per command/family (a faulty `consensus` adapter shouldn't require disabling `health`/`status`). State migration: `.ai` JSON/JSONL imported one-way, checksummed, with provenance; source files not deleted/rewritten until cutover complete + retention window expired.

## Interaction with the other 6 categories

| Category | Effect |
|---|---|
| Persistence/state migration | Easier (compat exposes peerhub state via translation, not pretending `.ai` files stay authoritative); harder (legacy IDs/in-flight ops need explicit mapping). |
| Request/attempt/session lifecycle | Easier if native lifecycle states stay canonical; harder where hub.py collapses multiple states into one exit code. |
| Routing/capability/leases | Easier (compat can reject requests with no honest peerhub equivalent); harder (old callers may assume implicit routing/weaker auth). |
| Output/errors/observability | Harder initially (legacy text/exit codes need a stable rendering contract); easier long-term (native JSON becomes canonical). |
| Consensus/governance | Easier if compat only covers verified proposal/vote flows; harder if old commands rely on file polling/implicit coordinator behavior. |
| Sessions/in-flight continuity | **Highest-risk interaction** — compat can't guarantee continuity unless hub.py session IDs map to peerhub session/request identities; needs explicit migration+recovery rules. |
| Cutover/packaging/ops | Easier with per-command-family feature flags/per-caller rollout; harder because two surfaces coexist during deprecation. |

## Required invariants (draft)

1. Existing daily dispatch stays operational throughout migration.
2. Peerhub has one canonical state model.
3. Compat layer never fabricates unsupported semantics.
4. Every compat command has a declared equivalence profile.
5. Native JSON/schema behavior is versioned.
6. Mutating commands are never dual-run against live state.
7. Every migrated caller has a tested rollback reference.
8. Compat removal requires caller-inventory evidence, not just doc updates.
9. Request correlation/idempotency prevents duplicate execution during fallback.
10. Legacy and native commands cannot silently operate on divergent authoritative states.

## Open questions requiring further dialectical rounds (not yet resolved)

- Exact initial compatibility command set (needs real caller-traffic/dependency measurement, not just guessed).
- Is `python hub.py ...` preserved literally, or does a wrapper executable become the transition entrypoint?
- Which legacy exit codes are contractual vs accidental?
- Byte-for-byte stdout required for any existing caller, or is semantic equivalence sufficient?
- Canonical peerhub JSON schema and versioning policy?
- Can hub.py session IDs be safely mapped to peerhub session IDs?
- How are in-flight hub.py operations handled at cutover?
- Which mutations may be imported/replayed vs must be abandoned with a recovery record?
- What telemetry is permitted for discovering real command traffic?
- Who may disable a compat command in production?
- Deprecation period and removal criterion?
- Does the project need compat for external/untracked callers that can't be inventoried?
- How are Windows `.bat`/PowerShell callers specifically verified?
- What constitutes equivalence for async commands whose output timing differs?
