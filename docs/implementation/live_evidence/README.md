# Live evidence (history)

The JSON files here are sanitized live-canary receipts committed up to v0.24.12. They stay as history; no test or workflow reads them.

From v0.24.13 the live gate writes `<date>-v<version>.json` to the git-ignored `.peerhub/work/live-gate/` and the file is attached to each GitHub release as a fourth asset instead of being committed. See "Releasing" in [CONTRIBUTING](../../../CONTRIBUTING.md).
