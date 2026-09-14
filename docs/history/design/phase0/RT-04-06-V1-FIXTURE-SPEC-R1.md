# RT-04..06 V1 Fixture Spec R1

AG/CX reviewed deterministic routing cases.

- RT-04: when an eligible non-terminal candidate exists, terminal/excluded
  candidates have zero automatic-routing weight and a recorded exclusion reason.
- RT-05: equal candidates use a seed derived from request ID and snapshot;
  identical inputs reproduce the exact selection and audit seed.
- RT-06: any config revision or admission snapshot drift before dispatch returns
  `CONFIGURATION_STALE` without dispatch, then plans anew against current state.

All cases use synthetic candidates and immutable snapshots; no provider call or
live routing state is allowed.  They are V1 TDD fixtures after Phase 0 ratification.
