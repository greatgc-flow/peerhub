from collections import deque
from dataclasses import dataclass


@dataclass
class _Job:
    job_id: int
    remaining: int
    priority: int
    used: int = 0


def sched_rr(jobs: list[tuple[int, int, int, int]], quantum: int) -> dict[int, int]:
    arrivals = sorted(jobs, key=lambda job: (job[1], job[0]))
    next_index = 0
    queues: dict[int, deque[_Job]] = {}
    finish: dict[int, int] = {}
    running: _Job | None = None
    time = 0

    def enqueue(now: int) -> None:
        nonlocal next_index
        while next_index < len(arrivals) and arrivals[next_index][1] == now:
            job_id, _, work, priority = arrivals[next_index]
            queues.setdefault(priority, deque[_Job]()).append(_Job(job_id, work, priority))
            next_index += 1

    while len(finish) < len(arrivals):
        if running is None and not queues:
            time = arrivals[next_index][1]
            enqueue(time)
        if running is None:
            priority = max(queues)
            running = queues[priority].popleft()
            if not queues[priority]:
                del queues[priority]

        next_arrival = arrivals[next_index][1] if next_index < len(arrivals) else None
        peers = queues.get(running.priority)
        if not peers:
            # An expiry alone cannot change the running job before the next arrival.
            elapsed = running.remaining
        else:
            # Skip complete rotations strictly before the next arrival or finish.
            if running.used == 0:
                rotation = (len(peers) + 1) * quantum
                shortest = min(running.remaining, min(job.remaining for job in peers))
                rounds = (shortest - 1) // quantum
                if next_arrival is not None:
                    rounds = min(rounds, (next_arrival - time - 1) // rotation)
                if rounds > 0:
                    work = rounds * quantum
                    running.remaining -= work
                    for job in peers:
                        job.remaining -= work
                    time += rounds * rotation
            elapsed = min(running.remaining, quantum - running.used)
        if next_arrival is not None:
            elapsed = min(elapsed, next_arrival - time)
        running.remaining -= elapsed
        running.used = (running.used + elapsed - 1) % quantum + 1
        time += elapsed

        if running.remaining == 0:
            finish[running.job_id] = time
            running = None
        enqueue(time)
        if running is not None:
            if running.used == quantum:
                running.used = 0
                queues.setdefault(running.priority, deque[_Job]()).append(running)
                running = None
            elif queues and max(queues) > running.priority:
                queues.setdefault(running.priority, deque[_Job]()).appendleft(running)
                running = None
    return finish
