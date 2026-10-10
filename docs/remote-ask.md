# Run a peer on another server (today, no new code)

Send work to the server with the required resources and environment by running
the existing `peerhub ask` command through key-based SSH. This recipe was NOT
tested end to end: no sshd was available when written.

## Choose the target

Keep a small local host/alias table; tags are operator declarations, not measured
capabilities. These are example names and paths, not detected installations.

| SSH host/alias | Tags | Remote Python | Remote workspace |
|---|---|---|---|
| `gpu-box` | gpu, os=linux, cli=codex | `/srv/peerhub/venv/bin/python` | `/srv/jobs/demo` |
| `win-box` | os=windows, cli=claude | `C:/PeerHub/venv/Scripts/python.exe` | `C:/PeerHub/jobs/demo` |

Select the host explicitly; do not infer GPU availability from a tag. Configure
each alias for the dedicated remote OS account and its SSH key. Verify the host
key before sending prompts. SSH setup/exit behavior and cross-shell examples
below are **unverified** in the permitted evidence; validate them on your hosts.

## Prepare the remote account

Use a dedicated low-privilege OS account and workspace. Never put secrets in its
environment. Keep readable files, CLI credentials, tools and network access as
limited as the job permits; a remote prompt can read what that OS account can
read. Read-only does not provide confidentiality.

The default omits `--writable`. Explicit read-only enforcement exists only for
Codex (`peerhub/extensions/adapters/base.py:164`); Claude (`:111–120`) and agy (`:206–215`)
have no explicit read-only restriction. `--writable` selects Codex
`workspace-write`, Claude `acceptEdits`, or agy `accept-edits` (`:119,164,212`).
The process inherits all environment variables (`:317`), including secrets.
Review finding (cx): these code references support the execution-boundary concerns.

Keep one local database per server; never share a SQLite file across machines.
Pass global `--db` before `ask`, and make `-w` a path on the remote server.
Explicit database selection takes precedence over workspace store selection
(`peerhub/cli/app.py:179–184`). The no-shared-file rule is an operating policy here.

Use the remote venv's absolute Python path with `-m peerhub`; do not assume an
interactive shell's venv is active. Ensure the provider executable is on that
account's noninteractive PATH (discovery: `peerhub/extensions/adapters/base.py`).
Install/authenticate the required provider under that restricted account.

## Run and retry

From a bash client, with a bash remote shell (example paths without spaces):

```bash
ssh -i ~/.ssh/peerhub_ed25519 -o BatchMode=yes gpu-box '/srv/peerhub/venv/bin/python -m peerhub --db /srv/peerhub/core.db ask cx "Summarize this workspace" -w /srv/jobs/demo --stream remote:demo-001 --request-id demo-001 --timeout-seconds 120 --json'
```

With `-o BatchMode=yes`, the host key must already be in `known_hosts` (connect
once interactively or use `ssh-keyscan`); otherwise the call fails.

From a PowerShell 5.1 client, when the Windows remote default shell is `cmd.exe`:

```powershell
ssh -i "$HOME/.ssh/peerhub_ed25519" -o BatchMode=yes win-box 'C:/PeerHub/venv/Scripts/python.exe -m peerhub --db C:/PeerHub/core.db ask cc \"Summarize this workspace\" -w C:/PeerHub/jobs/demo --stream remote:demo-001 --request-id demo-001 --timeout-seconds 120 --json'
$LASTEXITCODE
```

**UNVERIFIED:** PowerShell 5.1 and 7 differ in native-argument quoting; the safest
route for complex prompts is `--query-file` with the file placed on the remote,
or piping the prompt via a here-string/file.

When the remote default shell is PowerShell, the same no-space executable path
works as an example; for a quoted executable path, use its call operator `&`.
For bash, quote a space-containing executable path directly. `cmd.exe` uses
double quotes rather than bash single quotes; PowerShell has different expansion
and escaping rules. Both the local and remote shell parse the command. Do not
splice untrusted prompts into shell text. For complex prompts, stage a UTF-8
file on the remote host and use `--query-file` instead (`peerhub/cli/app.py:110,179`).
Cross-shell quoting and file transfer remain **unverified** here.

`--stream` names a durable conversation, not stdout streaming. Use a separate
stream per independent job: the default `peer:<id>:chat` shares history and a
claim with other asks (`peerhub/extensions/ask.py:71,80`; Review finding (ag)).
Repeat the exact command after a lost connection: preserve the host, database,
peer, stream, prompt, request ID and runtime binding/options. A completed retry
returns `recovered_terminal`; changed content/binding conflicts. Uncertain
execution is never automatically replayed. Do not invent a new ID to bypass
uncertainty; inspect evidence and reconcile first (`peerhub/extensions/ask.py:80–118`;
`peerhub/cli/app.py:112`). Idempotency does not promise that external effects never occur.

## Output, exit codes and timeouts

`--json` returns the result object, including status, certainty, request/stream/
peer IDs, record references and response; delivery details and usage are included
when available (`peerhub/extensions/ask.py:99–114`). Without it, available provider output
is printed incrementally; otherwise the response is printed at completion, and
unsuccessful results go to stderr (`peerhub/cli/app.py:185–204`). Files stay in the remote
workspace; this recipe specifies no automatic file transfer.

| PeerHub exit | Meaning (`peerhub/cli/app.py:204,398–412`) |
|---|---|
| 0 | delivered or recovered terminal |
| 1 | other ask status or generic error |
| 2 | idempotency conflict; argument parsing also uses argparse |
| 3 / 4 / 6 | CAS lost / storage error / schema version error |

`--timeout-seconds` bounds provider execution (default 60); it is not an SSH
connection timeout. Optional `--silence-timeout-seconds` aborts silent execution
and leaves uncertainty; `--max-output-bytes` defaults to 1,000,000
(`peerhub/cli/app.py:121–124`). A disconnect/timeout does not prove no work happened.
SSH's remote-exit propagation and transport-failure code 255 are **unverified**;
capture `$?` in bash or `$LASTEXITCODE` in PowerShell and inspect both stderr and
the JSON status before retrying.

## Private network

Tailscale SSH servers run on Linux and macOS only, NOT Windows
([Tailscale SSH](https://tailscale.com/docs/features/tailscale-ssh)). For this
key-based recipe, use ordinary OpenSSH over the private network. Windows hosts
need the built-in OpenSSH Server optional feature with key authentication;
that Windows setup claim is **unverified** in the permitted sources. Restrict
network access to the intended account/hosts; a private network does not replace
the account/workspace boundary (Review finding (cx)).

**UNVERIFIED Windows OpenSSH Server setup pointers:**

- Install the OpenSSH Server capability.
- Configure the `sshd` service start type and start the service.
- Configure the firewall rule for port 22.
- Configure key placement and ACLs, including `administrators_authorized_keys`
  for administrator accounts.
- Choose the default shell.

See Microsoft's [OpenSSH installation and first use](https://learn.microsoft.com/windows-server/administration/openssh/openssh_install_firstuse)
and [OpenSSH Server configuration](https://learn.microsoft.com/windows-server/administration/openssh/openssh_server_configuration).

## Verify before relying on it

- Check key login and host identity: `ssh -i <key> -o BatchMode=yes <host> whoami`.
- Check the remote interpreter: `ssh <host> '<remote-python> -m peerhub --version'`.
- Check flags: `ssh <host> '<remote-python> -m peerhub ask --help'`.
- Check provider PATH in bash: `ssh <host> 'command -v codex'`; in Windows cmd:
  `ssh <host> 'where.exe codex'`; in PowerShell:
  `ssh <host> 'powershell -NoProfile -Command Get-Command codex'`.
- After an intentional small ask (spends provider quota), inspect the store:
  `ssh <host> '<remote-python> -m peerhub --db <remote-db> diag health'`.
- Repeat the same small request ID and verify `recovered_terminal`, unchanged
  response references, and no second provider execution. Inspect account file
  permissions and environment locally without printing secrets.

Findings come from two independent read-only reviews (cx: security and protocol; ag: simplicity and operations) of a draft design on 2026-10-10; code references are relative to the peerhub package and were checked against version 0.13.0. Commands above are verification instructions, not
recorded successful runs. See [the proposed server design](design/a2a-inbound-server.md).
