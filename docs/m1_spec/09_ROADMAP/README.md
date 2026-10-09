# PeerHub Master Roadmap

This directory is the single SSOT/guide for **M1 → M2 → M3 → Optional Capability Tracks**.

Core principles:

```text
M1 Durable Communication
  ↓
M2 Durable Work Continuity
  ↓
M3 Federated Intelligent Collaboration
  ↓
Required roadmap complete
  ↓
Optional Capability Tracks (M4-A ... M4-L)
```

- M1~M3 are the baseline roadmap.
- M4 and beyond are not sequential mandatory milestones but **independent opt-in capability tracks**.
- The Core remains strictly four concepts to the end: `Peer / Stream / Record / Offset`.
- M2/M3/Optional features do not backflow into the Core.
- The machine-readable SSOT is `roadmap.json`.
- `roadmap.schema.json` and the package validator verify structure and boundaries.

## Crucial Locations to Remember

The existing `peerhub diag` **quota / rate-limit / runtime state view is M1**.

```text
M1 Observation -> Collect quota/rate_limit evidence
M1 Diag        -> View/display this evidence as strict read-only
M3 Routing     -> Use quota evidence in selection policy
M4-B           -> Actually allocate/coordinate quota/resources among multiple works
```

Therefore, we do not wait for M2/M3 to view quotas.
