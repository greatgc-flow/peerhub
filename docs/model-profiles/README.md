# Model profiles: one source, many users

`peerhub/config_data/model-defaults.toml` is the packaged runtime profile source;
workspace and global `models.toml` files override it per field. The resolver in
`peerhub/extensions/adapters/profiles.py` defines that precedence.

`model-profiles.json` (validated by `model-profiles.schema.json`) records the
provider/model refresh procedure and mirrors the ten packaged defaults (including
`ag.argon`, pinned to `gemini-4-argon-medium` on A-7D). The
manifest test checks parity with the TOML; it is not the runtime source for
effective workspace/global configuration. Read-only `peerhub profile list/show`
is designed to display the effective TOML values and each field's source.
Configured models do not prove current provider availability.

`peerhub profile list` also shows Known models (pool); JSON adds `known_models`
from the effective `exact_model_pools` registry. `agy models` lists vendor slugs;
pass one with `peerhub ask ag ... --model <slug> --writable`. The registry covers
flash (G-5H/G-7D), argon (A-7D), Claude opus/sonnet (3P-5H/3P-7D) and
`gpt-oss-120b-medium` (3P, unverified). Entries marked `unverified` are declared
mappings, not measured pool evidence. This is registry completeness only;
arbitrary-model monitor/readiness integration remains out of scope (#194).

Any AI CLI can use the manifest with one sentence: "read
docs/model-profiles/model-profiles.json and refresh the profiles". Claude Code
also has the skill `.claude/skills/refresh-model-profiles` that points to it.
`python -m tools.model_ping` checks which model really answers.
