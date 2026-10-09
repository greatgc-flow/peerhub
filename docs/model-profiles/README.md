# Model profiles: one source, many users

`model-profiles.json` (validated by `model-profiles.schema.json`) is the only place that defines the 12 profiles and the procedure to refresh them.
Any AI CLI can use it with one sentence: "read docs/model-profiles/model-profiles.json and refresh the profiles".
Claude Code also has the skill `.claude/skills/refresh-model-profiles` that points to it. `python -m tools.model_ping` checks which model really answers.

`cc.pro` maps to `claude-fable-5-1` and requires account usage credits. Do not assume availability from the profile table: verify the account with `python -m tools.model_ping cc.pro` (spends a little quota). The ping reports API failures such as `429 credits_required`; a configured profile is not proof that the account can use it.
