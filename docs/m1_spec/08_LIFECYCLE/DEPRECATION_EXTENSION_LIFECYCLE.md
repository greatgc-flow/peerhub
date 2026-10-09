# Deprecation / Extension Lifecycle

## Extension promotion

Do not immediately add a feature request to Core.

```text
signal
→ repeated/validated need
→ Extension candidate
→ boundary + schema + tests
→ isolated implementation
→ operation evidence
→ M2+ milestone review
```

Promotion to Core requires a separate decision supported by all of the following.

- A recurring essential need across multiple use cases
- Clear structural duplication or invariant violations if kept as an Extension
- Sufficiently verified failure semantics
- Migration/cutover impact analysis
- Benefits outweigh the existing Core simplicity

## Deprecation

```text
announce
→ observe usage
→ provide replacement/migration
→ warn
→ remove from advertised surface
→ remove implementation
→ retain historical evidence
```

Review usage evidence before abrupt removal.

## External provider/model/standard changes

Update Catalog for changing facts and Skill for procedures.
Consider Core schema changes only when the actual boundary changes.
