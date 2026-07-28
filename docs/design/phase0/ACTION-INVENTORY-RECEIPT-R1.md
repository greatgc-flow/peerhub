# Static Action Inventory Receipt R1

Status: proposed measured inventory receipt. This records identity, order,
ownership, and disposition only. It does not establish action compatibility,
argument/output parity, fixture adequacy, implementation authorization, or a
safe-abort classification.

## Inputs

| Input | SHA-256 |
|---|---|
| legacy Hub parser `_sys/core/hub.py` | `1a9f9c4a393989401aefeb60dbb3e24e8063ca558f1fcb595fb882a635a8d0b8` |
| `hub-actions-v1.csv` | `c6b21246eae48b0a34c5c7e024c2b927a693f53ca0a67724b9b4346a8bebdf98` |
| `action-fixture-policy-v1.csv` | `597550e12b6ac6041dddf1bf72edfb7262ec481f2e3339840e86d2499f006467` |
| fixture contract | `a7580d75dd6daed02f6f77309e669975c258bd8a1992de685c5186defa1496ee` |

## Measured result

- parser actions: 90; CSV rows: 90; unique CSV action names: 90;
- parser actions missing from CSV: none; CSV actions absent from parser: none;
- canonical ordered action-name vector SHA-256: UTF-8, parser order,
  LF-joined with one trailing LF, `2065c0b6de16cc39224bd3d364199383c2f625c1a6564e642fc853b76d76196d`;
  this is the frozen baseline digest in `PHASE0-COMPATIBILITY.md` §2.1;
- 13 unique action domains and 13 unique policy domains; no action domain lacks
  a policy row;
- dispositions: 82 `required`, 8 `compatibility-wrapper`.

## Decision boundary

Ratification may freeze this exact static inventory and its proposed v1 command,
ownership, and disposition columns. It must not claim that domain-level policy
fixtures prove each action, nor that any action is safe to abort or implemented.
Those require per-action schemas, action-specific evidence, and a later round.
