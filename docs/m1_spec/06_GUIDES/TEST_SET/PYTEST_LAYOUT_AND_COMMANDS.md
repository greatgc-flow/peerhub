# Pytest Layout / Commands

Recommended actual implementation layout:

```text
tests/
  architecture/
  schema/
  unit/
  property/
  integration/sqlite/
  concurrency/
  multiprocess/
  fault/
  bridge/
  observation/
  diag/
  e2e_fake/
  migration/
  security/
  meta/
  live/
  package/
  soak/
```

Recommended execution:

```bash
pytest -q -m "architecture or schema or unit or property or meta"
pytest -q -m "sqlite or concurrency or multiprocess or fault or migration"
pytest -q -m "bridge or observation or diag or e2e or security"
pytest -q -m "package"
pytest -q -m "live"          # requires quota/network/auth
pytest -q -m "soak"          # scheduled/non-blocking by default
```

Rules:
- sleep is prohibited for race/TTL; use deterministic barrier/manual clock.
- prioritize injectable VFS/adapter/process boundaries for fault over real destructive host faults.
- do not mix failure evidence of live/soak with deterministic gate results.
