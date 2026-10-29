---
name: refresh-model-profiles
description: Refresh PeerHub's latest AI models and model profiles from the installed CLIs, then ping-test them. Use when asked to update models/profiles, add/change/delete a profile, or check which model really answers.
---

The packaged `peerhub/config_data/model-defaults.toml` is the only store of profiles and observed versions;
`docs/model-profiles/model-profiles.json` (schema: `model-profiles.schema.json`) is the refresh recipe.
Read it and follow its `instructions` array exactly; it names the CLI commands to query (`sources`), the selection rules (`tier_rules`), the files to edit together (`update_targets`) and the checks to run.

Short form:
1. Query each peer CLI (`codex debug models`, `agy.exe models`; `claude` has no list, use known ids) and compare with `peerhub profile list`.
2. Show the diff and wait for approval before changing anything.
3. After approval edit the toml (new `defaults_revision` = `YYYY-MM-DD.N`, `observed_at`, `observed_versions`, each touched profile `source` = `'cli:<command>@<observed cli version> <observed_at>'` or `'known-id:<probe>@<version> <observed_at>'`); review the adapter docs. Add/change/delete a profile = one `[profiles."<peer>.<name>"]` table. Keep LF.
4. `python -m pytest -q tests/communication/adapters`.
5. With approval (uses quota): `python -m tools.model_ping [profile ...]` shows which model actually answered.
6. Do not commit, push or release unless asked. Record dated observations in CHANGELOG.md.
