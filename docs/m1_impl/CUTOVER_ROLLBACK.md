# Cutover rollback: the `peerhub` entrypoint selector

Rollback restores the entrypoint only. It never rewrites, migrates or downgrades M1 data (cutover gate item 7).

## Selector
The `peerhub` console script (`peerhub.cli:main`) reads the environment variable `PEERHUB_CLI`:

| Value | Effect |
|---|---|
| unset or empty | shipped default: `legacy` (the v0 hierarchy, unchanged) |
| `legacy` | the v0 hierarchy |
| `m1` | argv is forwarded to `peerhub.m1_cli.main` (same tree as `peerhub-m1`) |
| anything else | exit 2, one stderr line naming the allowed values, no traceback, nothing written |

- Environment only: there is no config-file support. The switch reads no file and opens no database.
- `peerhub-m1` is a separate script and is not affected by the variable.
- `PEERHUB_CLI_REPORT=1` prints `peerhub: cli selector=<value> (source: env|default)` to stderr (stdout, including `--version`, is unchanged).
- The shipped default stays `legacy`. Changing `DEFAULT_CLI_SELECTOR` in `peerhub/cli/selector.py` is the separate owner decision that flips the default; the candidate identity includes it.

## Roll back (return to the legacy CLI)
Remove the variable or set it to `legacy`, in the scope where it was set.

bash:
```
unset PEERHUB_CLI            # this shell
export PEERHUB_CLI=legacy    # or pin it explicitly
```
PowerShell:
```
Remove-Item Env:PEERHUB_CLI -ErrorAction SilentlyContinue
$env:PEERHUB_CLI = "legacy"
[Environment]::SetEnvironmentVariable("PEERHUB_CLI", $null, "User")   # persistent user-level value
```
cmd:
```
set PEERHUB_CLI=
set PEERHUB_CLI=legacy
setx PEERHUB_CLI ""            & rem persistent: an empty value means the default
```
Verify: `PEERHUB_CLI_REPORT=1 peerhub --version` reports `selector=legacy`; `peerhub --help` shows "Common workflows".

## Opt in (use the M1 CLI through `peerhub`)
bash `export PEERHUB_CLI=m1`, PowerShell `$env:PEERHUB_CLI = "m1"`, cmd `set PEERHUB_CLI=m1`. `peerhub --help` then lists `legacy-import`.

## What the flip does not do
M1 databases (`--db` files) are never read or written by the selector. `tests/m1/e2e/test_cut_selector.py::test_cut_007` flips both ways over a
populated M1 store and asserts its file bytes, state digest and directory listing are unchanged. Data written by M1 commands stays in the M1
store; the legacy CLI does not read it (no downgrade tool exists or is implied).
