# Monitor Compact 'peers' Dependency View (2026-10-16)

**Status:** Unanimous CX+AG consensus; supersedes heavier monitor/lineage designs.

## 1. Goal & Conceptual Model
Add ONE compact group `peers` to the monitor (Full + groups, in default set). It renders the linear dependency chain per peer for real peers (`cc`, `cx`, `ag`):
`quota -> profile/model [config layer] -> latest session [fresh|reused, gen] -> context [measured%|unknown]`
- Built ONLY from existing read-model facts (sessions, runtime/profile provenance, capacity observations, context).
- Missing facts render as `unknown` (or `STALE`). Never infer model-specific quota pool membership.
- Session reuse comes from recorded execution mode (`fresh` vs `reused`), not static `resumable` capability.

## 2. Screen Mocks

### Standard View (~80 cols)
```text
cc  quota: 5h: 82% (12m ago) -> cc.effort/claude-sonnet-5-5 [workspace]
    -> latest session: reused gen 1 -> context: unknown
cx  quota: 1d: 65% (4m ago)  -> cx.effort/gpt-6.1-sol [global]
    -> latest session: fresh gen 0  -> context: 45k (22%)
ag  quota: unlim (doc)       -> ag.effort/gemini-3.8-flash-med [packaged]
    -> latest session: none         -> context: unknown
```

### Narrow Terminal Fallback (Ordinary clipping, no width bands)
```text
cc: quota 82% | cc.effort/claude-sonnet-5-5 [ws] | reused gen 1 | ctx: unk
cx: quota 65% | cx.effort/gpt-6.1-sol [glob]     | fresh gen 0  | ctx: 45k (22%)
ag: quota unlim| ag.effort/gemini-3.8-flash [pkg]| none         | ctx: unk
```

## 3. Updatable Facts & Maintenance Procedure
- **Storage:** Model lists and documented quota-window facts live in `docs/model-profiles/model-profiles.json` (packaged fallback: `model-defaults.toml`).
- **Single source field:** Every fact specifies exactly ONE source:
  - CLI probe: `cli:<command>@<version> (<date>)`
  - Provider doc URL + checked date: `doc:<url>@<YYYY-MM-DD>`
  - Observation reference: `obs:<observation_id|ref>`
- **Provenance separation:** Config layer (`packaged`/`global`/`workspace`) is separate provenance; live quota stays observations.
- **Maintenance procedure:** Existing repo skill `refresh-model-profiles` (CLI probes + ping tests), extended to record source fields; parity test verifies every model entry has a valid source.

## 4. Intentional Non-Goals (Explicit Cut List)
Ruthlessly deferred or rejected to preserve 'so-very-simple' scope:
- Quota/model catalog framework
- Runtime web lookup
- Plugin framework
- Paging / input mode
- New DB tables
- Refresh service
- DB schema v2 beyond the version gate
- Live-evidence archive
- Round-robin fairness
- 2-line capacity summary
- reset_credit rows
- schema_migrations beyond minimal ledger
- Expired last-known display (show STALE/unknown)
- Width-band ladder
- Coverage-dimension redesign

## 5. PR List & TDD Plan

### Minimal PR Sequence
1. **PR 1 (`peers` group):** AG writes RED tests then GREEN; CX reviews; CC final review only.
2. **PR 2 (manifest source metadata + parity test + refresh-skill step):** CX drafts, AG applies.

### TDD RED Tests (PR 1 & PR 2)
- `test_monitor_peers_group_renders_dependency_chain`: Verifies rendering of quota -> model [layer] -> session -> context for cc, cx, ag.
- `test_monitor_peers_session_mode_recorded_not_resumable`: Asserts fresh vs reused comes from recorded execution mode, not resumable.
- `test_monitor_peers_unknown_fallback_without_inference`: Missing session, context, or quota renders unknown without inferring model-specific quota pool membership.
- `test_monitor_peers_narrow_terminal_clipping`: Validates one-line-per-peer fallback with ordinary clipping on narrow terminals without width bands.
- `test_model_profiles_manifest_source_metadata_present`: Parity test checking every model entry in model-profiles.json has a source field.
- `test_model_profiles_source_schema_validation`: Verifies source values conform to CLI probe, doc URL + date, or observation reference.
- `test_refresh_skill_records_provenance`: Asserts refresh-model-profiles skill records source fields during probe execution.
