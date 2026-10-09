# Provider / Model / CLI Drift Runbook

1. Establish drift through Observation or a live canary
2. Distinguish it from Core failure
3. Recollect current facts with the `refresh-model-catalog` Skill
4. Validate JSON Schema
5. Present the proposed change diff
6. adapter compatibility test
7. After approval, update Catalog/docs/tests together
8. Run a minimal live canary for changed profiles only
9. release
10. Close after an observation period
