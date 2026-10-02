# 공식 원본 링크 목록 — 2026-10-01 검증

아래 링크는 설계 결정에 참고한 **공식/원본 문서**입니다. 블로그·2차 요약보다 원본 사양을 우선합니다.

## 1. JSON Schema — Draft 2020-12

- 상태: published/current
- 확인 내용: The official specification page identifies Draft 2020-12 as the current published JSON Schema version.
- 원본:
  - https://json-schema.org/specification
  - https://json-schema.org/draft/2020-12/

## 2. Agent Skills — Open Agent Skills specification (reviewed 2026-10-01)

- 상태: open-standard
- 확인 내용: A Skill is a directory centered on SKILL.md with progressive disclosure; OpenAI documents compatibility with the open Agent Skills standard.
- 원본:
  - https://agentskills.io/specification
  - https://developers.openai.com/api/docs/guides/tools-skills

## 3. Model Context Protocol — 2026-07-28

- 상태: published
- 확인 내용: The 2026-07-28 MCP core is stateless, uses JSON-RPC 2.0, and defines tools/resources/prompts plus opt-in extensions such as Tasks and Skills.
- 원본:
  - https://modelcontextprotocol.io/specification/2026-07-28
  - https://blog.modelcontextprotocol.io/posts/2026-07-28/
  - https://tasks.extensions.modelcontextprotocol.io/specification/draft/tasks
  - https://skills.extensions.modelcontextprotocol.io/specification/stable/skills

## 4. Agent2Agent (A2A) Protocol — 1.0.0

- 상태: latest-released
- 확인 내용: Official A2A documentation identifies 1.0.0 as the latest released version and positions A2A as an interoperability protocol for independent agents.
- 원본:
  - https://a2a-protocol.org/v1.0.0/
  - https://a2a-protocol.org/dev/specification/

## 5. OpenAPI Specification — 3.2.1

- 상태: published 2026-09-10
- 확인 내용: OpenAPI 3.2.1 is the latest published OpenAPI specification as reviewed on 2026-10-01.
- 원본:
  - https://spec.openapis.org/oas/latest.html
  - https://spec.openapis.org/oas/v3.2.1.html

## 6. AsyncAPI Specification — 3.1.0

- 상태: published
- 확인 내용: AsyncAPI 3.1.0 describes message-driven APIs in a protocol-agnostic machine-readable form.
- 원본:
  - https://www.asyncapi.com/docs/reference/specification/v3.1.0
  - https://www.asyncapi.com/blog/release-notes-3.1.0

## 7. CloudEvents — 1.0.2 stable

- 상태: stable-release
- 확인 내용: The CloudEvents repository identifies v1.0.2 as the stable released core specification while main contains work in progress.
- 원본:
  - https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md
  - https://github.com/cloudevents/spec/blob/main/README.md

## 8. OpenTelemetry Semantic Conventions — current docs; overall status Development

- 상태: development
- 확인 내용: OpenTelemetry semantic conventions provide common trace/metric/log/resource names and the current specification page is marked Development.
- 원본:
  - https://opentelemetry.io/docs/specs/otel/semantic-conventions/
  - https://opentelemetry.io/docs/concepts/semantic-conventions/

## 9. CycloneDX — 1.7

- 상태: published
- 확인 내용: CycloneDX 1.7 is the current published CycloneDX specification in the reviewed official documentation.
- 원본:
  - https://cyclonedx.org/specification/overview/

## 10. Sigstore Cosign — current official tooling

- 상태: maintained
- 확인 내용: Cosign is Sigstore's recommended CLI for artifact signing and verification.
- 원본:
  - https://docs.sigstore.dev/quickstart/quickstart-cosign/
  - https://docs.sigstore.dev/cosign/

## 11. OpenAI Agents API — public beta/current 2026

- 상태: current
- 확인 내용: The Agents API models Agent, Environment, Session, and Events/items and supports continuing or steering durable sessions.
- 원본:
  - https://developers.openai.com/api/docs/guides/agents-api/overview

## 12. OpenAI Agents SDK — current 2026 docs

- 상태: current
- 확인 내용: The Agents SDK provides code-first agent runtime patterns with tools and context across steps.
- 원본:
  - https://developers.openai.com/api/docs/guides/agents/sdk

## 13. Claude Managed Agents — managed-agents-2026-04-01 beta

- 상태: beta
- 확인 내용: Claude Managed Agents separates Agent, Environment, Session and Events, and Agent definitions can include tools, MCP servers and Skills.
- 원본:
  - https://platform.claude.com/docs/en/managed-agents/quickstart
  - https://platform.claude.com/docs/en/managed-agents/sessions

## 14. Microsoft Agent Framework / Harness Agent — current 2026 docs

- 상태: current
- 확인 내용: Agent Framework distinguishes Agents, Harness Agents, Workflows and Integrations; Harness adds long-task scaffolding such as context compaction, memory, file access and observability.
- 원본:
  - https://learn.microsoft.com/en-us/agent-framework/overview/
  - https://learn.microsoft.com/en-us/agent-framework/concepts/harness

## 15. Google ADK / agents-cli — current 2026 docs

- 상태: current
- 확인 내용: Google's agent tooling supports Skills and a broader build/evaluate/deploy/observe workflow around agent projects.
- 원본:
  - https://google.github.io/agents-cli/
  - https://google.github.io/agents-cli/reference/skills/
  - https://google.github.io/adk-docs/
