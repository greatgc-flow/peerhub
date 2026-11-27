# Model profiles: one source, one recipe

`peerhub/config_data/model-defaults.toml` is the only store of packaged profiles, `exact_model_pools`,
`observed_at`, `observed_versions` and `defaults_revision`. See [profile configuration](../config-hierarchy.md#model-profiles) for overrides, effective profiles and cards.

[`model-profiles.json`](model-profiles.json) (schema [`model-profiles.schema.json`](model-profiles.schema.json), `schema_version` 2) is only the refresh
recipe: CLI `sources`, `tier_rules`, `update_targets` and `instructions`. It holds no profile values.

## Add, change or delete a profile

1. Edit one `[profiles."<peer>.<name>"]` table in the toml (`selection_mode = "pinned"`, `model`,
   optional `reasoning_effort`, `source`, `checked`, `pools`, `pools_source`). For a new model also add
   an `[exact_model_pools."<model>"]` entry. Deleting a profile removes its table. Keep files LF.
2. Run `python -m pytest -q tests/communication/adapters`. Tests assert invariants, not values, so no
   other file changes; `profile list` reads the toml (the guide no longer prints a profile table).

### Optional profile fields (0.25.0)

All four are optional; the loader checks their format in every layer (an invalid value is a configuration error).
`peerhub profile list` shows them in its `NOTES` column and `profile show` as rows with the layer that set them.

| Key | Values | Default | Meaning |
|---|---|---|---|
| `alias_of` | `<peer>.<name>` of a profile of the same peer | unset | The profile takes its target's binding (`selection_mode`, `model`, `reasoning_effort`, `pools`) as defaults. Aliases resolve one level. The card is the alias's own. A text `ask` on a stderr terminal prints one notice per alias and process unless `PEERHUB_NO_RECEIPT=1`. |
| `effort_override` | nonempty string | unset | Effort for an alias. Higher layers win across this key and `reasoning_effort`. |
| `suggest` | `true` / `false` | `true` | Legacy key. Either boolean is accepted and ignored for planning. |
| `measurement` | `measured` / `unmeasured` | `measured` | `unmeasured` = no recorded measurement for the model id (the API peers `ca`, `oa` and `ga` until each has one). |

Every `ca.*`, `oa.*` and `ga.*` profile has `measurement = "unmeasured"`.
See [Compatibility](../commands.md#compatibility) for the packaged Codex alias.

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

## Refresh from installed CLIs

Follow the recipe's `instructions` in order. Review the proposed diff before editing. Use `python -m tools.model_ping [PROFILE ...]` only with approval because it spends quota. For one exact model without changing overlays, use `python -m tools.model_ping --peer cc --model MODEL [--effort EFFORT]`. Record dated observations in CHANGELOG.md.
