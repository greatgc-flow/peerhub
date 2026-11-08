# 0.24.1 plan (vote: ACK or NACK:<item> <reason>, <=4 lines; you may read the code)
#304 (cx design, read .peerhub/work/ph304-r1.txt for detail):
A1 Keep C6 precedence --db > PEERHUB_DB > nearest upward .peerhub/core.db. Only when that misses: ask git (argument-list subprocess, bounded timeout) for the common dir of the effective workspace/cwd, derive the MAIN worktree root, search it and its parents; source = discovered.
A2 An implicit selection that finds no store never creates one: before any directory/lineage/DB/provider work, exit 5 `[store_missing]` + `next:` (pass --db or set PEERHUB_DB, or run from the main checkout). Creation only with explicit --db / PEERHUB_DB on creation-capable commands.
A3 Tests: linked worktree + subdirectory + ask -w; local store wins; nested repos; non-git / git missing / malformed metadata; Windows spaces/Unicode; implicit miss leaves no artifacts and makes no provider call; explicit bootstrap still works.
#303 (cc triage, not reproduced; comment posted):
B1 CcSpec parses result.permission_denials and surfaces them as denied_actions (tool name + command string, redacted to the first 120 chars); an empty reply with denials => existing denied_empty status (#300) for cc too.
B2 Lesson `cc-allow-rules`: exact-match Bash rules vs model command shape; prefer prefix rules (Bash(tool:*)).
R Release 0.24.1 (patch; A2 is a behavior change for implicit misses -> CHANGELOG 'Changed' + migration line). Implementation: cx (A1-A3), cc (B1-B2), disjoint files; review: the other peer + ag.argon.
## Adopted amendments (unanimous after amendments)
B1' sanitize with the existing redaction helper BEFORE truncating to 120 chars; tests cover emitted, persisted and replayed denied_actions; the "ag denied" note becomes provider-neutral ("<peer> denied tool action(s)").
A2' `peer register` without --db in a fresh dir now exits 5: update README quickstart (line ~35) and report_missing_store next: hint in the same change; CHANGELOG migration line names this case.
