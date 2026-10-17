# Store Discovery Compatibility

## Precedence and Discovery Contract

Store selection follows strict deterministic precedence:
1. `PEERHUB_DB` environment variable (when environment lookup is permitted).
2. Explicit CLI flag (`--db PATH`).
3. Discovered workspace store: searches `.peerhub/core.db` in `<cwd>`, then ascending each parent directory.
4. Workspace root store (when `--workspace` is supplied): `<workspace>/.peerhub/core.db`.
5. Default path (`.peerhub/core.db`).

## Breaking Changes & History

### October 2026 (Audit Item O-1)
- **Removed implicit `<cwd>/peerhub` subfolder lookup**: Store discovery previously walked into `<cwd>/peerhub/.peerhub/core.db`. This broke root-rename resilience and caused workspace store pollution when running from workspace root.
- Owner approval: October 12, 2026 audit (O-1).
- Discovered stores now require `.peerhub/core.db` in the repository/workspace root, or explicit `--db PATH` / `PEERHUB_DB`.
