# One Source Multi Use + Second Brain 준비

## 원칙

```text
Procedure -> Agent Skill
Changing fact -> JSON catalog
Selection/binding policy -> declarative config/policy
Shape -> JSON Schema
Collaboration truth -> Stream/Record
Long/short-term memory -> future Memory Extension
```

## 전역 지침 지양
Skill FILES are the procedure source, Records are the lifecycle/provenance authority, and the SQLite catalog is a rebuildable projection (peerhub/extensions/skills.py).
Per-CLI materialization/sync (claude/codex/agy) is DEFERRED until a concrete consumer needs it (design-only, not a defect).
`AGENTS.md`를 model/catalog/운영지식 DB로 사용하지 않습니다.

## Skill 예
- token-efficient-collaboration
- refresh-model-catalog
- future compatibility verification

## Catalog 예
- runtime/model IDs;
- model/runtime capability facts and supported reasoning efforts;
- profile selection/binding is a separate declarative policy/config projection;
- capabilities;
- quota pool metadata;
- evidence/observed_at.

## Second Brain
향후 Memory Extension은 Record를 source로:

```text
short-term context
episodic memory
semantic memory
preference/profile memory
```

를 생성할 수 있지만 source refs/provenance/supersession을 유지합니다.

Procedural memory는 Skill로 유지합니다. Catalog JSON에는 refresh 절차나 운영 지시를 넣지 않습니다.
