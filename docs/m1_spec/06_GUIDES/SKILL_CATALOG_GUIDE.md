# Skill / Catalog Operation Guide

## Skill = HOW
Task procedures, verification sequences, iterative workflows.

## Catalog = WHAT
Current facts regarding changes in models/CLI/capabilities/quota pools.

## JSON Schema = SHAPE
Machine validation of Catalog and extension manifests.

## One Source Multi Use
Skill FILES are the procedure source, Records are the lifecycle/provenance authority, and the SQLite catalog is a rebuildable projection (peerhub/extensions/skills.py).
Per-CLI materialization/sync (claude/codex/agy) is DEFERRED until a concrete consumer needs it (design-only, not a defect).

## Global instruction
Place only the minimal stable constraints that must always be applied in project-global files.
