# Wave 0 report (26 ids: META-001..004, ARCH-001..005, SCH-001..015, PROP-007/008)

RED run (before production changes): 12 failed / 14 passed. Reasons:

| id | red reason | fix (GREEN) |
|---|---|---|
| ARCH-001 | `peerhub/m1/cli.py` imported `peerhub.extensions.observation_and_diag` | CLI composition root moved to `peerhub/m1_cli.py` |
| SCH-001..006, 011, 012, 013 | no strict wire boundary (`peerhub.m1.wire` missing); models accepted omitted required fields, bad dates, dup members/targets, version != 1.0 | added `peerhub/m1/wire.py` (schema validation before model), packaged schema copies in `peerhub/m1/schemas/`, model validators (RFC3339 dates, unique members/targets, Literal version) |
| SCH-014 | non-JSON/ non-str-key bodies not uniformly rejected before persistence | `assert_json_value` in models/store |
| SCH-015 | NaN/Infinity accepted (json.dumps default allow_nan) | strict `allow_nan=False` + finite check before digest/row |
| META-001..004, ARCH-002..005, SCH-007..010, PROP-007/008 | GREEN on first run (spec package pre-validated / production already conforming) | none |

False-green found and fixed during RED: `pytest.raises(Exception)` swallowed the missing-module ImportError (SCH-002/011/013); rejection now requires ValueError/TypeError.
GREEN: `pytest tests/m1 tests/unit/m1` = 41 passed (26 catalog ids + 1 drift guard + 14 existing unit). validate_package PASS. `tools/m1_traceability.py --upto 0` rc=0.
