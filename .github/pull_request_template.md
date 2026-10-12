## Summary
<!-- Concise description of changes. Follow Conventional Commits in your commit messages. -->

## Incident and Feedback Traceability
<!-- For fixes stemming from an operational incident, drift, or user feedback, link the full chain: -->
- **Incident / Issue**: <!-- e.g., #123, ESC/#80, or blocked_uncertain stream failure -->
- **Lesson Learned**: <!-- Which 5-whys rule or design principle was derived? -->
- **Changed Documentation**: <!-- Link to updated specification, README, or doc under docs/ -->
- **Regression Test**: <!-- Link to the deterministic test asserting failure prevention -->

## Verification
- [ ] Deterministic tests pass: `python -m pytest -q`
- [ ] Static type check passes: `pyright`
- [ ] Repo hygiene verified (no hardcoded absolute paths, all commands have examples)
