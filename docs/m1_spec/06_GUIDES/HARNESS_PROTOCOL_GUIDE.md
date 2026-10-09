# Harness / MCP / A2A Integration Guide

## Harness
managed/local agent runtime is the Session Bridge target.

## MCP
When a boundary between AI ↔ Tool/Data/Context arises, it's an Extension.
Do not bypass the Core API to directly modify the DB.

## A2A
When a remote independent agent arises, `A2A Adapter -> Peer`.
Do not copy the A2A Task state machine into Core.

## OTel
An exporter that sends Observation to an external telemetry backend.
The OTel semantic convention does not become the Core DB schema.
