# Plan 0.24.0 — monitor + CLI unification (2026-11-08)

Consensus: cx.effort, cc.deepthink, ag.argon — unanimous ACK after 4 rounds (independent r1, cross-critique r2, vote r3, final ACK r4).
P0 measured: model_ping 9/9 PASS (ag.deepthink skipped: 3P-7D <1%); CLI versions ag 1.5.3, cc 2.1.319, cx 0.167.0; no model changes.

Resolutions of r3 objections: P1 cx wording; P2 verified overlay-only (~/.peerhub/config/models.toml) -> no code change; M2 cx (INSUFF, '-' only for unavailable); C3 cx (one 'Advanced: stream, record, offset' help line); C6 cc alternative + cx stderr rule; M3 cc addition.
P0 refresh-model-profiles first; release blocks on measured facts.
P1 no profile rename/repoint; guide: while 3P is low, explicitly use ag.argon only for tasks its measured capabilities support, else defer.
P2 cx.terra untouched (user overlay). P3 F-7D: no profile; monitor shows "unmapped".
M1 drop OVERVIEW, one Coverage footer. M2 STATE: INSUFF (dim), '-' only for unavailable, highlight THREAT/STALE, fix alignment; JSON unchanged.
M3 full short codes (STALE:window, PROBE:8d, DRIFT:ag,cc,cx) + `peerhub explain <code>` pointer; each code gets an explain entry in config_data/lessons.toml.
M4 pool->consumer profile column, configured-vs-measured labelled. M5 one staleness line per peer + next command; hide "context: unknown"; header shows resolved DB path+source.
C1 compact human text default regardless of TTY; universal --json; --format hidden alias; schemas unchanged.
C2 roots/verbs/batch stable, no new aliases. C3 one compact Advanced help line. C4 no new verbosity tiers.
C5 errors: one stderr line + outcome code + next: hint, nonzero exit.
C6 DB: --db > PEERHUB_DB > nearest upward .peerhub/core.db (nearest wins, no multi-candidate error); resolved path+source (explicit|env|discovered|default) on stderr/header, never polluting --json stdout; guide documents PEERHUB_DB.
R 0.24.0; Breaking: non-TTY default + --db/PEERHUB_DB precedence swap, with --json migration line. TDD for C1/C5/C6/M1-M5. Impl: cx (code) + cc (review) + ag (docs/guide); I run tests locally/CI.

## ag usability addendum (unanimous ACK, 2 rounds)
Evidence: ag failed 3/4 asks today (shell denied #300; read_file denied with trusted -w; resumed stream fabricated paths); inline --query-file + "no tools" worked.
A1 One ag recipe (<=6 lines) first in `guide` ag section and `explain ag`: inline content via --query-file and say "no tools"; -w = trusted repo root (prerequisite, not a guarantee); --writable every turn (#140); one task per ask; verify flash claims; after an empty/denied_empty reply never resume that stream (overrides stream reuse) - new stream with content inlined.
A2 denied_empty `next:` hint = new stream + inline needed content via --query-file; do not resume.
A3 Merge ag-empty-reply-causes + ag-permission-limit into the recipe entry; record E2 as open observation linked to its issue.
A4 (trust-list preflight) dropped: E2 shows it would pass and give false confidence.
