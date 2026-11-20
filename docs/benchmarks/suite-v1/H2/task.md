# H2

Reply with exactly one fenced Python code block tagged `python`. Use the
standard library only. Do not use tools or include prose outside the block.

Implement `solve_game(n: int, edges: list[tuple[int, int, int]], start: int) -> tuple[str, int]`.

Two players alternate moving a token along directed edges of an `n`-node graph (`0 .. n-1`), starting at `start`.
Each tuple `(u, v, w)` in `edges` is a directed edge `u -> v` with integer weight `w >= 0`. Parallel edges (same `(u, v)` with possibly different weights) and self-loops `(u, u, w)` may exist.

- A player with no outgoing edges on their turn immediately loses.
- Infinite play is a draw.
- At every turn, the player to move prefers a forced win to a draw, and a
  draw to a forced loss.
- Among strategies that force a win, the eventual winner minimizes the total
  weight of all edges traversed before the game ends, in the worst case over
  the losing player's choices. The eventual loser maximizes that total.
  These objectives apply on each player's turns. A winning strategy must
  force termination, even when a cycle has total weight zero.

Return `("WIN", cost)`, `("LOSE", cost)`, or `("DRAW", -1)` for the player to move at `start`.
Limits: `1 <= n <= 20_000`; `0 <= len(edges) <= 50_000`; `0 <= w <= 10**9`.
The hidden cases include deep graphs, cycles, and costs larger than 32-bit integers.
