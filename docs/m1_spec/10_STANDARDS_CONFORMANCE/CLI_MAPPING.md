# CLI Mapping

```json
{
  "root": "peerhub",
  "preferred_patterns": [
    "peerhub <noun> <verb> [subject] [options]",
    "peerhub diag [--json]",
    "peerhub status [--json]"
  ],
  "shared_flag_semantics": [
    "--help/-h",
    "--json",
    "--dry-run/--apply where a mutation is previewable"
  ],
  "numeric_exit_codes": "ADAPT; semantic classes standardized first"
}
```
