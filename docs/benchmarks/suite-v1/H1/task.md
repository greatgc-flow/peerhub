# H1

Reply with exactly one fenced Python code block tagged `python`. Use the
standard library only. Do not use tools or include prose outside the block.

Implement `sched_rr(jobs: list[tuple[int, int, int, int]], quantum: int) -> dict[int, int]`.

Simulate a single-CPU preemptive priority round-robin scheduler with time quantum `q = quantum >= 1`.
Each job is `(job_id, arrive, work, prio)` with distinct `job_id`, `arrive >= 0`, `work >= 1`, integer `prio` (higher = higher priority). Each priority level has a FIFO ready queue. Each job tracks `used` (time used in its current quantum, initially `0`).

At any timestamp `t` where an event occurs, apply steps in strict order:
1. Completion: if the running job's remaining work is `0`, record `finish[job_id] = t` and vacate CPU.
2. Arrivals: enqueue all jobs with `arrive == t` at the back of their priority queue (`used = 0`) in ascending `job_id` order.
3. Quantum expiry: if a job is still running and `used == q`, vacate CPU, reset `used = 0`, and append it to the back of its priority queue (after step 2).
4. Preemption: if a job is still running (`0 < used < q`) and a strictly higher-priority queue is non-empty, vacate CPU and push it to the FRONT of its priority queue, preserving `used`.
5. Dispatch: if CPU is vacant and any queue is non-empty, pop the front of the highest-priority non-empty queue and run it.

Return `{job_id: finish_time}`.
Limits: `len(jobs) <= 400`; `0 <= arrive <= 10**12`; `1 <= work, quantum <= 10**12`; `|prio| <= 10**9`.
The hidden cases include runs with too many quantum expirations to simulate individually.
