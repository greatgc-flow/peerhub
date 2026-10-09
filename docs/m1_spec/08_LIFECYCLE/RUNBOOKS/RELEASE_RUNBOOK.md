# Release Runbook

1. Confirm the requirement/test/catalog revision freeze
2. Run G0~G4
3. artifact build + hash
4. fresh environment clean-install
5. real-provider advertised surface canary
6. known risks/deferred trigger review
7. Create a release evidence index
8. Check publish gate dependencies
9. publish
10. Start stabilization observation
11. Follow `ROLLBACK_RUNBOOK.md` immediately for an invariant breach
12. Conduct a Post-release Review after observation ends
