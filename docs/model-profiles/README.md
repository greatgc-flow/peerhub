# Model profiles: one source, one recipe

`peerhub/config_data/model-defaults.toml` is the only store of packaged profiles, `exact_model_pools`,
`observed_at`, `observed_versions` and `defaults_revision`. Workspace and global `models.toml` files
override it per field (precedence in `peerhub/extensions/adapters/profiles.py`). `peerhub profile
list/show` displays the effective values and each field's source. Configured models do not prove
current provider availability.

`model-profiles.json` (schema `model-profiles.schema.json`, `schema_version` 2) is only the refresh
recipe: CLI `sources`, `tier_rules`, `update_targets` and `instructions`. It holds no profile values.

## Add, change or delete a profile

1. Edit one `[profiles."<peer>.<name>"]` table in the toml (`selection_mode = "pinned"`, `model`,
   optional `reasoning_effort`, `source`, `checked`, `pools`, `pools_source`). For a new model also add
   an `[exact_model_pools."<model>"]` entry. Deleting a profile removes its table. Keep files LF.
2. Run `python -m pytest -q tests/communication/adapters`. Tests assert invariants, not values, so no
   other file changes; guide and `profile list` read the toml.

Personal opt-in profile (not packaged), in `~/.peerhub/config/models.toml`:

    schema_version = 1
    [profiles."cx.terra"]
    selection_mode = "pinned"
    model = "gpt-6.1-terra"
    reasoning_effort = "low"
    pools = ["X-5H", "X-7D"]
    pools_source = "unverified"

`peerhub profile list` also shows Known models (pool); JSON adds `known_models` from the effective
`exact_model_pools` registry. `agy models` lists vendor slugs; pass one with
`peerhub ask ag ... --model <slug> --writable`. Entries marked `unverified` are declared mappings, not
measured pool evidence. This is registry completeness only; arbitrary-model monitor/readiness
integration remains out of scope (#194).

Any AI CLI can use the recipe with one sentence: "read docs/model-profiles/model-profiles.json and
refresh the profiles". Claude Code also has the skill `.claude/skills/refresh-model-profiles`.
`python -m tools.model_ping` checks which model really answers (effective profiles, opt-ins included).
