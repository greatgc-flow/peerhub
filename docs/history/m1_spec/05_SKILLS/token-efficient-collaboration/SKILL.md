---
name: token-efficient-collaboration
description: Use for long-running or quota-sensitive PeerHub collaboration when status, handoff, review, or continuation should minimize repeated context without dropping important constraints or evidence.
---

# Token-efficient collaboration

1. Prefer delta since the Peer's Offset over full transcript restatement.
2. For status use: completed / current / blocked / next / evidence.
3. Reference existing Records/artifacts instead of copying large content.
4. Keep the latest user redirect and critical constraint explicit.
5. Do not omit safety/technical constraints merely to save tokens.
6. If evidence is missing, say UNKNOWN instead of guessing.
7. This Skill does not modify global project instructions.
