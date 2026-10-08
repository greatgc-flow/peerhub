---
name: refresh-model-profiles
description: Refresh PeerHub's latest AI models and the 12 model profiles (cc/cx/ag x standard/effort/deepthink/pro) from the installed CLIs, then ping-test them. Use when asked to update models/profiles or check which model really answers.
---

The single source of truth is `docs/model-profiles/model-profiles.json` (schema: `model-profiles.schema.json`).
Read it and follow its `instructions` array exactly; it names the CLI commands to query (`sources`), the selection rules (`tier_rules`), the files to edit together (`update_targets`) and the checks to run.

Short form:
1. Query each peer CLI (`codex debug models`, `agy.exe models`; `claude` has no list, use known ids) and compare with `profiles`.
2. Show the diff and wait for approval before changing anything.
3. After approval edit every `update_targets` file together (new `revision` = `YYYY-MM-DD.N`, also `defaults_revision` in the toml).
4. `python -m pytest -q tests/communication/adapters` (manifest vs toml vs doc tables).
5. With approval (uses quota): `python -m tools.model_ping [profile ...]` shows which model actually answered.
6. Do not commit, push or release unless asked.
