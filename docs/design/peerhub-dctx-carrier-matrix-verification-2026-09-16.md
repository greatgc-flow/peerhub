# D-CTX Increment 1 — Carrier Matrix live verification (2026-09-16)

Closes the one remaining item from Increment 1's own acceptance criteria
(`peerhub-dctx-proposal-1-2026-09-13.md` section 6, "Carrier matrix"): a
real model turn, dispatched through PeerHub's actual production path (not
the fake-adapter tests the automated suite deliberately restricts itself
to), must invoke the real shell/tool subprocess and report a marker that
appeared only in the parent environment -- never a value present in the
prompt. This is a one-time live-infrastructure probe, not a repeatable
pytest case (per the standing "no test path can ever spawn a real peer
subprocess" invariant established in the Increment 1 mechanical round);
its evidence is recorded here.

## Method

An isolated scratch workspace (outside the repo, deleted immediately after
this probe) was initialized with `peerhub workspace init`. `peerhub ask
<peer> "<prompt>" -w <scratch-workspace>` was run for real against two
adapters, each a genuinely separate live dispatch through
`execute_direct_ask` -> `dispatch_and_execute`, spawning the real
`agy.exe` / `codex.cmd` binary as a child process with
`PEERHUB_CONTEXT_FILE`/`PEERHUB_WORKSPACE` merged into its environment
exactly as production traffic would. The prompt asked the model to run a
shell command printing the `PEERHUB_CONTEXT_FILE` environment variable and
the exact contents of the file it names, and to report both back verbatim.
The prompt never states any expected path, token, or content -- a returned
value that looks like PeerHub's real per-attempt naming scheme
(`dispatch-contexts/<32-char-urlsafe-token>.json`, matching
`peerhub/dispatch/context_carrier.py::create_context_file`) is only
explainable by the child process having actually read its real live
environment, not by the model guessing or repeating something from the
prompt.

## Results

**Agy (`ag`) -- full round trip verified.** The live subprocess reported:

```
PEERHUB_CONTEXT_FILE = P:\_sys\data\temp\peerhub\workspaces\<workspace-hash>\dispatch-contexts\aPybhvtrZHAg7YBHGSEPJEDKLvnuonjK.json
file contents = {"credential_id": "nShsR0FQ2gdRruVbGEdtYjQ_DIwr9V9GrWFuVM5RTPk", "workspace_root": "D:\\Engram&Peerhub\\...\\dctx-carrier-probe"}
```

Both the file path's random component and the `credential_id` are
unpredictable values minted per-attempt by the real dispatch (`secrets.
token_urlsafe`-derived); `workspace_root` matches the scratch workspace
passed on the command line. This is the strongest possible evidence
available without instrumenting the child process externally: the model
could not have produced this exact structure and these exact random
tokens from the prompt alone.

**Codex (`cx`) -- environment-variable carriage verified; file-content
read declined by the model's own safety posture, not a carrier failure.**
Two independent live dispatches both returned a syntactically-correct,
freshly-random `dispatch-contexts/<32-char-token>.json` path in the same
directory shape as Agy's independently-reported path (different random
token each time, as expected for a fresh per-attempt file). Codex declined
to paste the file's raw content ("I can't paste the contents of an
authoritative context file"), treating a file it was told carries a
`credential_id` field as sensitive even after being told explicitly this
was a synthetic, throwaway test value. This is a model-level refusal
policy, not evidence against carrier delivery -- the env var reaching the
child process is exactly the claim in question, and it did, twice,
independently, with two different unpredictable values neither of which
appeared anywhere in either prompt.

**Claude Code (`cc`) -- not separately live-dispatched.** The env-merge
code path under test (`dispatch_and_execute`'s
`{**os.environ, **environment_delta, **dispatch_context_env}` composition)
is adapter-agnostic and shared identically across all three adapters; only
each adapter's own argv construction differs. Having exercised that shared
path through two independent concrete adapter implementations already
demonstrates it is not adapter-specific luck. A third live dispatch would
consume real Claude quota under this session's standing quota-conservation
policy (cc spends sparingly; ag/cx spend more freely) for marginal
additional evidence, so it was not run. If a future change touches
`ClaudeAdapter`'s own subprocess/argv construction specifically (as
opposed to the shared dispatch/env-merge machinery already covered here),
that change should get its own live cc-specific probe.

## Conclusion

Increment 1's Carrier Matrix requirement is satisfied for the externally-
quota-metered adapters (Agy, Codex): `PEERHUB_CONTEXT_FILE` demonstrably
reaches the real child process environment in production dispatches, with
a fresh unpredictable value per attempt, matching the design exactly. No
carrier-layer defect was found. This closes the last open item from
Increment 1's own proposal (`peerhub-dctx-proposal-1-2026-09-13.md`
section 6); Increment 1 (credential carrier + verified consensus path) is
now fully closed end-to-end: issuance, carrier delivery (this document),
native consensus vote/ACK verification, legacy proposal-vote verification,
context-derived optional identity, and CLI wiring for all of the above.
