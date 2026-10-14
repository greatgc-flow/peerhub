# Model profiles: one source, many users

`peerhub/config_data/model-defaults.toml` is the packaged runtime profile source;
workspace and global `models.toml` files override it per field. The resolver in
`peerhub/extensions/adapters/profiles.py` defines that precedence.

`model-profiles.json` (validated by `model-profiles.schema.json`) records the
provider/model refresh procedure and mirrors the nine packaged defaults. The
manifest test checks parity with the TOML; it is not the runtime source for
effective workspace/global configuration. Read-only `peerhub profile list/show`
is designed to display the effective TOML values and each field's source.
Configured models do not prove current provider availability.

Any AI CLI can use the manifest with one sentence: "read
docs/model-profiles/model-profiles.json and refresh the profiles". Claude Code
also has the skill `.claude/skills/refresh-model-profiles` that points to it.
`python -m tools.model_ping` checks which model really answers.
