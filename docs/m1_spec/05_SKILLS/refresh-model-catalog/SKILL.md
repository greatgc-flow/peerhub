---
name: refresh-model-catalog
description: Use when an AI CLI/provider or model set may have changed and PeerHub's structured model/capability catalog needs evidence-based refresh without editing global agent instruction files.
---

# Refresh model catalog

1. Capture installed CLI/runtime version.
2. Read official provider/runtime source and local runtime capabilities.
3. Compare against the current catalog.
4. Run the smallest safe real canary for ambiguous availability.
5. Keep unobserved fields UNKNOWN.
6. Produce a candidate JSON catalog revision.
7. Validate JSON Schema.
8. Show diff + evidence.
9. Promote only after compatibility tests.
10. Never encode model facts in AGENTS.md or shared behavior prompts.

## Separation guardrail

- Catalog JSON stores observed facts and evidence only.
- JSON Schema defines shape only.
- Refresh/verification procedure stays in this Skill.
- Profile selection/tier policy stays in declarative policy/config, not in mutable fact records.
