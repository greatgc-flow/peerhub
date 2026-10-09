# Configuration and store selection

This page describes the current public CLI. The legacy `config`, `node
bind-profile`, and `backup` CLI commands were retired; historical designs are
retained in [the design index](design/README.md). See the
[README](../README.md#store-selection-and-existing-data) for store discovery.

## Store

The workspace store is `.peerhub/core.db`. Select a path with global `--db PATH`
before the subcommand, or `PEERHUB_DB`. An explicit missing path is never
redirected to another store. `ask --workspace DIR` selects that workspace's
store unless a database was explicitly selected. Read-only commands do not
create a missing store. `peerhub diag health` reports the selection and path.

## Model profiles

`ask --profile` resolves profile fields from workspace, then global, then
packaged defaults (highest priority first):

| Layer | File |
|---|---|
| Workspace | `<workspace>/.peerhub/config/models.toml` |
| Global | `~/.peerhub/config/models.toml` |
| Packaged | `peerhub/config_data/model-defaults.toml` |

`PEERHUB_CONFIG_HOME` replaces the global config directory. A missing file in
that directory contributes no values; it does not select another global root.
Malformed selected files are errors. Profile entries merge by field, with
`schema_version = 1` and entries under `[profiles."<peer>.<profile>"]`.
An explicit `--model` or `--effort` overrides the resolved profile defaults.
Without `--profile`, the vendor CLI supplies its defaults unless explicitly
overridden. Configuration does not prove model availability.

See the [model profile manifest](model-profiles/model-profiles.json) for
profile IDs, models, efforts, and the refresh procedure, and
[profiles.py](../peerhub/extensions/adapters/profiles.py) for the loader.
Keep credentials with the vendor CLI's authentication mechanism.
