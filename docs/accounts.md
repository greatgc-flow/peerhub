# Account-bound peers

Bind a new Peer to an existing vendor configuration directory for `cc` or `cx`:

```powershell
peerhub --db .peerhub/core.db account bind --peer reviewer --provider cc --alias work --root C:\accounts\reviewer
peerhub account list --json
peerhub account show --peer reviewer --show-root --json
peerhub ask reviewer "Review this code" --stream review --request-id review-001
```

`--root` must be absolute and already exist. Log in through the vendor CLI using
that root before asking. Binding does not log in, read credentials, or call a
provider. `ask` executes the provider and may spend quota. Use the same global
`--db` selection for binding and execution. Builtin aliases keep their default
account behavior; bind a new peer name, never `cc`, `cx`, `ag` or their aliases.
ag is unsupported because its authentication lives in the OS keyring rather
than an isolated CLI configuration root.

Bindings are immutable: there is no rebind, unbind or epoch counter. A new
account or moved root requires a new Peer and a new root. Out-of-band re-login
cannot be detected without reading credentials. Keep each root on the same
vendor login; a different identity requires binding a new Peer and root.
Overwriting a bound peer with `peer register` wipes its marker and makes later
asks refuse. Recover with a new peer and root, rather than editing the registry.

At launch, roots must contain no symlink, junction or reparse point in their
path. They cannot equal, contain or sit within another bound root, the ambient
`CLAUDE_CONFIG_DIR`/`CODEX_HOME`, `~/.claude`, `~/.codex`, or the ask workspace.
Comparison includes normalized real paths and case folding. This is a
single-user boundary; same-user directory swaps between validation and process
creation remain outside the guarantee.

Bound cc children receive `CLAUDE_CONFIG_DIR`; cx children receive `CODEX_HOME`.
Parent variables beginning with `ANTHROPIC_`, `CLAUDE_CODE_`, `OPENAI_` or `CODEX_`
are scrubbed case-insensitively, except the root variable being set. Results
and receipts retain removed variable names in `env_scrubbed` for success, refusal,
errors, timeouts and replay, without values.
Parent credentials and the root path (including JSON-escaped, slash and case
variants) are redacted from adapter evidence.
Parent process variables and unbound peer behavior remain unchanged.

Account identity participates in session and request binding. Sessions cannot
cross accounts. Retrying an unchanged settled request can replay after its root
is removed, without spawning or accessing credentials. Launch validation occurs
after acquiring the claim; validation failure proves NOT_STARTED and releases
the claim. `runtime=` injection is refused for bound peers.

Account list/show hide root paths unless `--show-root` is passed. Backups of the
database contain the stored root path. PeerHub never reads, hashes or copies
credential files. Prompts and responses still persist in the selected store.

Quota collection and monitor probing continue to cover only builtin default
accounts. Bound-peer quota is not observed; do not infer that default-account
quota applies to a bound peer. No automatic account failover is provided.
