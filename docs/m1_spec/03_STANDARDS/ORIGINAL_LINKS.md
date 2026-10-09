# Official Source Links — Verified 2026-10-01

The links below are **official/source documents** consulted for design decisions. Prefer original specifications over blogs and 2nd-hand summaries.

## 1. JSON Schema — Draft 2020-12

- Status: published/current
- Verified findings: The official specification page identifies Draft 2020-12 as the current published JSON Schema version.
- Sources:
  - https://json-schema.org/specification
  - https://json-schema.org/draft/2020-12/

## 2. Agent Skills — Open Agent Skills specification (reviewed 2026-10-01)

- Status: open-standard
- Verified findings: A Skill is a directory centered on SKILL.md with progressive disclosure; OpenAI documents compatibility with the open Agent Skills standard.
- Sources:
  - https://agentskills.io/specification
  - https://developers.openai.com/api/docs/guides/tools-skills

## 3. Model Context Protocol — 2026-07-28

- Status: published
- Verified findings: The 2026-07-28 MCP core is stateless, uses JSON-RPC 2.0, and defines tools/resources/prompts plus opt-in extensions such as Tasks and Skills.
- Sources:
  - https://modelcontextprotocol.io/specification/2026-07-28
  - https://blog.modelcontextprotocol.io/posts/2026-07-28/
  - https://tasks.extensions.modelcontextprotocol.io/specification/draft/tasks
  - https://skills.extensions.modelcontextprotocol.io/specification/stable/skills

## 4. Agent2Agent (A2A) Protocol — 1.0.0

- Status: latest-released
- Verified findings: Official A2A documentation identifies 1.0.0 as the latest released version and positions A2A as an interoperability protocol for independent agents.
- Sources:
  - https://a2a-protocol.org/v1.0.0/
  - https://a2a-protocol.org/dev/specification/

## 5. OpenAPI Specification — 3.2.1

- Status: published 2026-09-10
- Verified findings: OpenAPI 3.2.1 is the latest published OpenAPI specification as reviewed on 2026-10-01.
- Sources:
  - https://spec.openapis.org/oas/latest.html
  - https://spec.openapis.org/oas/v3.2.1.html

## 6. AsyncAPI Specification — 3.1.0

- Status: published
- Verified findings: AsyncAPI 3.1.0 describes message-driven APIs in a protocol-agnostic machine-readable form.
- Sources:
  - https://www.asyncapi.com/docs/reference/specification/v3.1.0
  - https://www.asyncapi.com/blog/release-notes-3.1.0

## 7. CloudEvents — 1.0.2 stable

- Status: stable-release
- Verified findings: The CloudEvents repository identifies v1.0.2 as the stable released core specification while main contains work in progress.
- Sources:
  - https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md
  - https://github.com/cloudevents/spec/blob/main/README.md

## 8. OpenTelemetry Semantic Conventions — current docs; overall status Development

- Status: development
- Verified findings: OpenTelemetry semantic conventions provide common trace/metric/log/resource names and the current specification page is marked Development.
- Sources:
  - https://opentelemetry.io/docs/specs/otel/semantic-conventions/
  - https://opentelemetry.io/docs/concepts/semantic-conventions/

## 9. CycloneDX — 1.7

- Status: published
- Verified findings: CycloneDX 1.7 is the current published CycloneDX specification in the reviewed official documentation.
- Sources:
  - https://cyclonedx.org/specification/overview/

## 10. Sigstore Cosign — current official tooling

- Status: maintained
- Verified findings: Cosign is Sigstore's recommended CLI for artifact signing and verification.
- Sources:
  - https://docs.sigstore.dev/quickstart/quickstart-cosign/
  - https://docs.sigstore.dev/cosign/

## 11. OpenAI Agents API — public beta/current 2026

- Status: current
- Verified findings: The Agents API models Agent, Environment, Session, and Events/items and supports continuing or steering durable sessions.
- Sources:
  - https://developers.openai.com/api/docs/guides/agents-api/overview

## 12. OpenAI Agents SDK — current 2026 docs

- Status: current
- Verified findings: The Agents SDK provides code-first agent runtime patterns with tools and context across steps.
- Sources:
  - https://developers.openai.com/api/docs/guides/agents/sdk

## 13. Claude Managed Agents — managed-agents-2026-04-01 beta

- Status: beta
- Verified findings: Claude Managed Agents separates Agent, Environment, Session and Events, and Agent definitions can include tools, MCP servers and Skills.
- Sources:
  - https://platform.claude.com/docs/en/managed-agents/quickstart
  - https://platform.claude.com/docs/en/managed-agents/sessions

## 14. Microsoft Agent Framework / Harness Agent — current 2026 docs

- Status: current
- Verified findings: Agent Framework distinguishes Agents, Harness Agents, Workflows and Integrations; Harness adds long-task scaffolding such as context compaction, memory, file access and observability.
- Sources:
  - https://learn.microsoft.com/en-us/agent-framework/overview/
  - https://learn.microsoft.com/en-us/agent-framework/concepts/harness

## 15. Google ADK / agents-cli — current 2026 docs

- Status: current
- Verified findings: Google's agent tooling supports Skills and a broader build/evaluate/deploy/observe workflow around agent projects.
- Sources:
  - https://google.github.io/agents-cli/
  - https://google.github.io/agents-cli/reference/skills/
  - https://google.github.io/adk-docs/
