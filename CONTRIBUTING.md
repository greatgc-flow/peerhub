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

The full suite takes about 14 minutes. For everyday work, skip the one test that re-runs
the whole suite (CI and releases still run it); this takes about 10 minutes:

```bash
pytest -q --deselect tests/communication/package/test_rel_ci_dag.py::test_rel_005_deterministic_suite_passes_without_provider_clis_credentials_or_network
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

## Decisions and lessons

- Stay on 0.x until the public API boundary is defined.
- Do not add a `peerhub feedback` command; the issue templates and `diag --json`
  provide enough feedback and diagnostic context.
- Do not provide a winget package; pipx, uv and pip installation are documented.
- `--workspace` is ask-only. Use global `--db` to select the store for `monitor`
  and `observation refresh`.
- PEP 740 attestations are published by the pinned pypa publishing action;
  publication was verified on PyPI for 0.13.0.
- Defer xdist parallelism until tests sharing temporary directories or databases
  are verified to work in parallel.
- Windows limits: cx `--writable` cannot write into owner-only-ACL directories,
  and taskkill may report unkillable children. Use normally created directories
  for writable canary tests.
- The legacy v0 109-command disposition and Korean togo roadmap were superseded
  by `docs/m1_spec` and `tools/command_inventory.py`. They were deleted because
  they contained no unique content.

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

### Local live gate

Real provider tests are excluded from default runs and CI. They require
`PEERHUB_LIVE=1` plus an explicit marker selection. Even with that opt-in,
the live fixtures skip when `CI` is set. Missing peer binaries skip the new
per-peer ask measurements; authentication failures and missing usage fail.

```powershell
# All stages, complete release evidence (requires all three peer CLIs).
python -m tools.live_gate --yes

# Optional measurements for one peer; outside the release catalog.
python -m tools.live_gate --yes --only canary --peer cx

# Direct opt-in: -m live selects exactly the six M1 live-tier tests.
# Select slow/e2e separately, or canary for optional ask measurements.
$env:PEERHUB_LIVE = "1"
python -m pytest -q -m live tests/communication/live
python -m pytest -q -m "canary and cx" tests/communication/live
```

The four optional `canary` scenarios measure each of cc, cx and ag through public `ask`
with `--profile <peer>.standard`: text and positive input/output token usage,
native `--resume` recall on a second turn, `--writable` sentinel creation,
and cancellation after committed STARTED evidence. The cancellation test
invokes the CLI in-process and delivers durable `control.cancel` through the
Bridge to the real adapter, since there is no public cancel CLI command.
It requires confirmed process-tree termination and no successful response.

These four scenarios make at most five asks per peer (15 across all peers),
with tiny prompts, bounded output and 240-second provider timeouts. They use
temporary workspaces and databases, make no quota probes or test-level retries,
and leave no persistent measurement files. Existing canaries add their own
calls and sanitized evidence. Standard profiles resolve workspace/global
overrides, so review local profile policy before spending quota. A single-peer
gate run selects peer-marked tests and omits aggregate cases. `--only canary`
writes separate `canary.xml` without a G3 stamp; these scenarios have no
release-catalog IDs and are excluded from default tests, CI and the release
gate. `--only live --peer <peer>` remains available for partial release
evidence; skipped or missing catalog cases cannot satisfy the full G3 gate.

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
