# Post-release Review

## Timing

Set a stabilization window suited to the release rather than a fixed number of days.
Require sufficient observation as risk increases, such as for data migration, Bridge semantics, or provider adapter changes.

## Review questions

### Correctness
- Were there any zero-tolerance invariant breaches?
- Were any unknown/uncertain states mistaken for success?

### Reliability
- Did restart/recovery/catch-up work as expected?
- Was manual recovery repeated?

### Provider / Observation
- Was there provider/model/CLI drift?
- Were quota/rate/availability represented accurately?

### Operations
- Is the runbook sufficient for response?
- Was evidence collection sufficient?
- Were alerts actionable?

### Product simplicity
- Did new features unnecessarily expand Core?
- Can any logic move to config/Skill/Catalog/Extension?

### Learning
- Are new regression tests needed?
- Should the exception/interaction inventory be expanded?
- Which items should become inputs to the next release?

## Closure

Each finding must link to a terminal disposition or the next lifecycle item.
“Reviewed” alone is not a closed state.
