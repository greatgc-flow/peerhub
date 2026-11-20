import heapq


def solve_game(n: int, edges: list[tuple[int, int, int]], start: int) -> tuple[str, int]:
    rev: list[list[tuple[int, int]]] = [[] for _ in range(n)]
    rem = [0] * n
    for u, v, w in edges:
        rev[v].append((u, w))
        rem[u] += 1

    state: list[str | None] = [None] * n
    cost = [0] * n
    best_win = [10**30] * n
    max_lose = [0] * n
    pq: list[tuple[int, int, int]] = [(0, 0, u) for u in range(n) if rem[u] == 0]
    heapq.heapify(pq)

    # Finalize a win at its cheapest settled losing successor. Finalize a
    # loss after every outgoing edge leads to a settled win.
    while pq:
        d, kind, v = heapq.heappop(pq)
        if state[v] is not None:
            continue
        state[v] = "LOSE" if kind == 0 else "WIN"
        cost[v] = d
        if kind == 0:
            for u, w in rev[v]:
                if state[u] is None and d + w < best_win[u]:
                    best_win[u] = d + w
                    heapq.heappush(pq, (d + w, 1, u))
        else:
            for u, w in rev[v]:
                if state[u] is None:
                    if d + w > max_lose[u]:
                        max_lose[u] = d + w
                    rem[u] -= 1
                    if rem[u] == 0:
                        heapq.heappush(pq, (max_lose[u], 0, u))

    outcome = state[start]
    return ("DRAW", -1) if outcome is None else (outcome, cost[start])
