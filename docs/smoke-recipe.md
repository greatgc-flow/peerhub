# Installed-CLI profile ping

Run from an existing directory with installed provider CLIs and authentication; pings spend quota.
A ping must not create a checkout, branch, or design package.

```powershell
pip install -U peerhub
peerhub guide
peerhub profile list
New-Item -ItemType Directory -Force .peerhub | Out-Null
peerhub --db .peerhub/smoke.db peer register --peer cx --name "Smoke cx"
```

`profile list` already emits JSON; there is no `--json` flag. The store parent directory must exist before initialization.
Inspect `peerhub --db .peerhub/smoke.db diag quota --json`; only if needed, refresh once with `peerhub --db .peerhub/smoke.db observation refresh`.
For each listed profile, set `$peer` and `$profile` to its peer and profile ID; execute exactly one nonce ask:

```powershell
$nonce = [guid]::NewGuid().ToString('N')
$writeFlags = @(); if ($peer -eq 'ag') { $writeFlags = @('--writable') }
peerhub --db .peerhub/smoke.db ask $peer "Reply only with nonce $nonce. Do not modify files or create a checkout, branch, or design package." --profile $profile --stream "smoke-$profile-$nonce" --request-id "ping-$nonce" --timeout-seconds 7200 --silence-timeout-seconds 7200 @writeFlags --json
```

Keep each `--stream` persistent and record it in one result matrix: profile | peer | stream | nonce match | status/error | verdict.
Exhausted provider => **BLOCKED/UNVERIFIED**: no retries, credit resets, or substitution. A failed council member is not assent. If uncertain, inspect health and records before any follow-up; use a new stream. See [peer limits](peer-limits.md).
