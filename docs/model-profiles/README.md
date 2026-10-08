# Model profiles: one source, many users

`model-profiles.json` (validated by `model-profiles.schema.json`) is the only place that defines the 12 profiles and the procedure to refresh them.
Any AI CLI can use it with one sentence: "read docs/model-profiles/model-profiles.json and refresh the profiles".
Claude Code also has the skill `.claude/skills/refresh-model-profiles` that points to it. `python -m tools.model_ping` checks which model really answers.
