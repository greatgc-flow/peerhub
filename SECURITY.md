# Security Policy

## Supported Versions

Only the latest minor release line receives security updates.

| Version | Supported          |
| ------- | ------------------ |
| 0.19.x  | :white_check_mark: |
| < 0.19  | :x:                |

## Reporting a Vulnerability

Please report suspected security vulnerabilities privately rather than through public issues or pull requests.

Use [GitHub Private Vulnerability Reporting](https://github.com/greatgc-flow/peerhub/security/advisories/new) for `greatgc-flow/peerhub`. Private vulnerability reporting is enabled for this repository.

### Report Guidelines

- **No secrets in reports:** Do not include live API keys, tokens, credentials, or proprietary secrets in vulnerability reports or logs. Sanitize or redact all sensitive information prior to submission.
- Include clear reproduction steps, environment details, and an assessment of impact.

## Scope

### In Scope

- **Store handling:** SQLite store (`core.db`) transaction integrity, path traversal protections, migration safety, and database file access.
- **Sandbox and write boundaries:** Peer execution sandbox enforcement, `--writable` boundary controls, and isolation guarantees.
  - *Known limitation:* Headless `ag` (`antigravity-cli`) does not enforce a read-only sandbox when `--mode accept-edits` is omitted; provider-level restrictions apply (see issue #140).

### Out of Scope

- Vulnerabilities in third-party provider services or model APIs themselves.
- Unsupported or superseded versions.

## Response Expectations

Maintainers review security reports on a best-effort basis. Confirmed vulnerabilities will be remediated in coordinate releases alongside security advisories.
