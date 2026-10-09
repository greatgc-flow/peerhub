# Contributing to peerhub

Thanks for helping improve peerhub, the Python library and CLI for coordinating
multiple AI peers.

## Development setup

peerhub requires Python 3.11–3.14. Clone the repository, then install its
development dependencies with the editable install documented in the
[README setup section](README.md#develop-from-this-checkout):

```bash
pip install -e .[dev]
```

Before opening a pull request, run:

```bash
pytest -q
pyright
```

## Code and tests

Read [CONVENTION.md](CONVENTION.md) before making code changes. It records the
project's coding rules. Runtime code is grouped into `core`, `extensions` and
`cli`; tests are grouped by communication, continuity and collaboration.
Shared fixtures and fake CLIs live under `tests/communication/harness`.

## Branches, commits, and pull requests

Start from `main` and use a focused, lower-case topic branch. Existing branches
use slash namespaces and commonly include a date, such as
`docs/feedback-loop-2026-09-23`.

Use concise Conventional Commit subjects, following the repository's existing
history: `fix(cli): correct adapter detection`, `docs: clarify setup`, or
`feat(governance): add a workflow`. Open a focused pull request to `main` that
explains the change and the checks you ran.

## Reporting issues and requesting features

Use the GitHub bug-report or feature-request templates when applicable. They
collect the reproduction and environment details needed to work on an issue.
Include lessons/feedback from using the docs or CLI: what was confusing, what
you tried, and the wording or behavior that would have helped. See the
[development/operation closed loop](docs/m1_spec/USAGE_GUIDE.md#developmentoperation-closed-loop).

## Releasing

1. Open a version-bump PR updating `peerhub/_version.py` and the current
   `v0.x.y` line in `docs/STATUS.md`. Wait for CI to be green: `gh pr checks`
   must show zero pending or failed checks before merging. Never merge before
   checks finish.
2. From a **clean checkout of the merged main commit**, run
   `python -m tools.live_gate --yes`. This spends provider quota, refuses CI,
   and requires `agy`, `claude` and `codex` on PATH and logged in. It writes
   `g3.xml`, `g3-slow.xml` and `g3-e2e.xml` under `.peerhub/work/live-gate/`,
   stamped to HEAD. Require a successful run of all three stages.
3. Write release notes to a file, then create a draft with all three assets.
   Replace `<HEAD-SHA>` with the full output of `git rev-parse HEAD` and
   `<notes-file>` with the notes path; use the bumped version for `vX.Y.Z`:

   ```bash
   gh release create vX.Y.Z --draft --target <HEAD-SHA> --title vX.Y.Z --notes-file <notes-file> .peerhub/work/live-gate/g3.xml .peerhub/work/live-gate/g3-slow.xml .peerhub/work/live-gate/g3-e2e.xml
   gh release edit vX.Y.Z --draft=false
   ```

Publishing the draft triggers `.github/workflows/publish.yml`: `verify` runs
G0/G1/G2/G7 verification, including the M2/M3 catalog tests in G2. `build` and
`live-validation` then run in parallel: build produces and tests the wheel and
sdist, while live-validation fetches the three release assets. After build, the
8-cell matrix tests the built distributions on Ubuntu/Windows with Python
3.11–3.14. `release-evidence` waits for verify, build, live-validation and the
matrix. `publish` uses PyPI OIDC and runs only for a published release.

To exercise the workflow without publishing:

```bash
gh workflow run publish.yml --ref <branch> -f evidence_tag=<tag>
```

When the tag's live evidence is bound to a different commit from `<branch>`,
expect `release-evidence` to HOLD on G3. A manual dispatch never publishes to
PyPI; matching evidence still has to meet the freshness and PASS requirements.

Pushing workflow-file changes through gh-backed authentication requires a token
with the `workflow` scope. Refresh it with:

```bash
gh auth refresh -h github.com -s workflow
```

## Release evidence

`tools/release_evidence.py` requires every blocking M1 catalog test and every
M2/M3 catalog test (`docs/m2/test-catalog.m2.json` and
`docs/m3/test-catalog.m3.json`, gate G2) to pass. Live `LIVE-*` tests must also
pass, including the LIVE-007 slow and LIVE-008 e2e canaries. Skipped or missing
tests do not count as passes; G6 soak is non-blocking.

Candidate identity is the source revision plus sorted package digests. Source
verification and live JUnit files bind to the source revision; G4 package and
matrix evidence also binds to the exact built distributions. Freshness TTL and
clock-skew tolerance come from `docs/m1_impl/release-policy.json`. Package and
matrix tests use `PEERHUB_DIST_DIR` to test the built distributions.
`tests/conftest.py` records `@pytest.mark.catalog_id` markers as JUnit properties.

After **any edit under `docs/m1_spec`**, run:

```bash
python docs/m1_spec/tools/seal_package.py
```

This rebuilds `MANIFEST.json` and `SHA256SUMS.txt`, then validates the package.
A product-maturity item may be `VERIFIED` only with a `git_sha`
`candidate_identity` whose value is a full 40-hex Git SHA.

## Working with AI peers

Ask peers with `peerhub ask <ag|cx|cc> ...` (choose one peer).
`--writable` permits file edits and is off by default. It maps to cc
`--permission-mode acceptEdits`, cx `-s workspace-write` (default
`-s read-only`), and ag `--mode accept-edits`.

Keep prompts in English, split large tasks into small requests, and treat peer
output as drafts. Verify every change yourself. For cx reviews, run pytest and
Pyright yourself and apply `.github/` edits yourself: restricted cx sandboxes
can prevent those operations even when file edits are enabled. Sandbox limits
are environment-dependent, rather than a guarantee made by `--writable`.
Before peer calls, run `peerhub observation refresh` followed by `peerhub diag`
and watch the 7-day pace; distribute work so cc is not the heaviest user.

## Development notes

- The frozen M1 catalog tests define Bridge behaviour. Fix code to satisfy the
  contract; do not weaken tests to accommodate a bug. Catalog edits under
  `docs/m1_spec` require `seal_package.py` as described above.
- Ordinary text uses LF in the Git index. Preserve each file's existing working
  tree line endings; frozen package files have explicit `.gitattributes` rules.
- If the Windows Git credential helper blocks a push, use:

  ```powershell
  git -c credential.helper= -c "credential.helper=!gh auth git-credential" push -u origin HEAD
  ```

- `tools/a2a_sdk_interop.py` needs a separate virtualenv with
  `a2a-sdk[http-server]` and `uvicorn`. From this checkout, run
  `python -m tools.a2a_sdk_interop <venv-python>` to start the SDK server with
  that interpreter and check interoperability.
- `python -m tools.model_ping` spends a little quota per profile. Pass profile
  IDs, for example `python -m tools.model_ping cc.pro`, to limit the checks.
