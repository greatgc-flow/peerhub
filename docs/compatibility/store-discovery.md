# Store Discovery Compatibility

## Precedence and Discovery Contract

Store selection follows strict deterministic precedence:
1. `PEERHUB_DB` environment variable (when environment lookup is permitted).
2. Explicit CLI flag (`--db PATH`, or when discovery is disabled).
3. Conditional store discovery:
   - **When `workspace_root` is specified** (e.g. `ask --workspace DIR`): discovery searches that workspace root *exclusively* (`<workspace_root>/.peerhub/core.db`). If present, source is `discovered`; if absent, source is `workspace-default` (`<workspace_root>/.peerhub/core.db`). It never walks `<cwd>` or parent directories.
   - **When `workspace_root` is omitted**: discovery searches `.peerhub/core.db` in `<cwd>`, then ascending each parent directory. If found, source is `discovered`; if no candidate is found, source is `default` (`.peerhub/core.db`).

## Breaking Changes & History

### October 2026 (Audit Item O-1)
- **Removed implicit `<cwd>/peerhub` subfolder lookup**: Store discovery previously walked into `<cwd>/peerhub/.peerhub/core.db`. This broke root-rename resilience and caused workspace store pollution when running from workspace root.
- Owner approval: October 12, 2026 audit (O-1).
- Discovered stores now require `.peerhub/core.db` in the repository/workspace root, or explicit `--db PATH` / `PEERHUB_DB`.
