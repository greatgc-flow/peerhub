# One Source Multi Use + Second Brain Preparation

## Principles

```text
Procedure -> Agent Skill
Changing fact -> JSON catalog
Selection/binding policy -> declarative config/policy
Shape -> JSON Schema
Collaboration truth -> Stream/Record
Long/short-term memory -> future Memory Extension
```

## Avoid Global Instructions
Skill FILES are the procedure source, Records are the lifecycle/provenance authority, and the SQLite catalog is a rebuildable projection (peerhub/extensions/skills.py).
Per-CLI materialization/sync (claude/codex/agy) is DEFERRED until a concrete consumer needs it (design-only, not a defect).
Do not use `AGENTS.md` as a database for models/catalogs/operational knowledge.

## Skill Examples
- token-efficient-collaboration
- refresh-model-catalog
- future compatibility verification

## Catalog Examples
- runtime/model IDs;
- model/runtime capability facts and supported reasoning efforts;
- profile selection/binding is a separate declarative policy/config projection;
- capabilities;
- quota pool metadata;
- evidence/observed_at.

## Second Brain
A future Memory Extension can use Records as its source to generate:

```text
short-term context
episodic memory
semantic memory
preference/profile memory
```

while preserving source refs/provenance/supersession.

Keep procedural memory in Skills. Do not put refresh procedures or operational instructions in Catalog JSON.
