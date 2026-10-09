# Skill / Catalog 운용 가이드

## Skill = HOW
작업 절차, 검증 순서, 반복 workflow.

## Catalog = WHAT
현재 모델/CLI/capability/quota pool 등 변동 사실.

## JSON Schema = SHAPE
Catalog와 extension manifest를 기계 검증.

## One Source Multi Use
Skill FILES are the procedure source, Records are the lifecycle/provenance authority, and the SQLite catalog is a rebuildable projection (peerhub/extensions/skills.py).
Per-CLI materialization/sync (claude/codex/agy) is DEFERRED until a concrete consumer needs it (design-only, not a defect).

## Global instruction
항상 적용해야 하는 극소수 안정 제약만 프로젝트 전역 파일에 둡니다.
