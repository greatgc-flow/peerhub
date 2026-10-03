# Pytest Layout / Commands

권장 실제 구현 레이아웃:

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

권장 실행:

```bash
pytest -q -m "architecture or schema or unit or property or meta"
pytest -q -m "sqlite or concurrency or multiprocess or fault or migration"
pytest -q -m "bridge or observation or diag or e2e or security"
pytest -q -m "package"
pytest -q -m "live"          # quota/network/auth 필요
pytest -q -m "soak"          # scheduled/non-blocking by default
```

규칙:
- race/TTL은 sleep 금지, deterministic barrier/manual clock.
- fault는 real destructive host fault 대신 injectable VFS/adapter/process boundary 우선.
- live/soak의 실패 evidence는 deterministic gate 결과와 섞지 않음.
