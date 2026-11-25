"""Authoritative plan acceptance and execution evidence backed by Core Records."""
from __future__ import annotations

import datetime
import hashlib
import json
import math
import sqlite3
from typing import Any, cast

from peerhub.extensions.orchestration_model import Plan, PlanBoundExceededError, PlanStateTransitionError, StepExecutionResult


def _plan_json(plan: Plan) -> str:
    return json.dumps(plan.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)


class RecordPlanJournal:
    """Authoritative evidence through public Core ports, no private database.

    Requires a dedicated single-author Stream so append idempotency enforces
    one owner per invocation attempt across executor instances. Evidence refs
    can bind an Artifact or approval Record; their verification belongs to the
    accepting authority, not to invented journal approval logic.
    """

    def __init__(self, store: Any, stream_id: str, author_peer_id: str) -> None:
        self.store = store
        self.stream_id = stream_id
        self.author_peer_id = author_peer_id

    def _check_owner(self) -> None:
        if self.store.get_stream(self.stream_id).members != [self.author_peer_id]:
            raise PlanStateTransitionError("Durable execution requires a dedicated single-author stream.")

    def _records(self, plan_id: str) -> list[Any]:
        self._check_owner()
        records: list[Any] = []
        after = 0
        while True:
            page = self.store.read_records(self.stream_id, after_position=after, limit=256)
            if not page:
                break
            for record in page:
                body = record.body
                if record.kind.startswith("m3.orchestration.") and isinstance(body, dict) and cast(dict[str, Any], body).get("plan_id") == plan_id:
                    if record.author_peer_id != self.author_peer_id:
                        raise PlanStateTransitionError("Plan evidence has a foreign authority.")
                    records.append(record)
            after = page[-1].position
        return records

    def _append(self, plan: Plan, kind: str, suffix: str, body: dict[str, Any]) -> Any:
        self._check_owner()
        def owner_guard(conn: sqlite3.Connection, *, stream_id: str, peer_id: str) -> None:
            members = [row[0] for row in conn.execute(
                "SELECT peer_id FROM stream_members WHERE stream_id=?", (stream_id,)
            )]
            if members != [peer_id] or peer_id != self.author_peer_id:
                raise PlanStateTransitionError("Execution stream ownership changed before durable append.")
        return self.store.append_record(
            guard=owner_guard,
            stream_id=self.stream_id, author_peer_id=self.author_peer_id,
            kind=f"m3.orchestration.{kind}", body={"plan_id": plan.plan_id, **body},
            idempotency_key="orchestration:" + hashlib.sha256(json.dumps([plan.plan_id, suffix]).encode()).hexdigest(),
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )

    def accept_plan(self, plan: Plan, *, accepted_by: str,
                    evidence_refs: list[str] | None = None) -> str:
        if not accepted_by:
            raise PlanStateTransitionError("Accepted plan requires an explicit accepting authority.")
        if not math.isfinite(plan.bounds.timeout_seconds) or plan.bounds.timeout_seconds <= 0:
            raise PlanBoundExceededError("Accepted plan requires a finite positive deadline.")
        if evidence_refs is not None and any(not isinstance(cast(object, ref), str) or not ref for ref in evidence_refs):
            raise PlanStateTransitionError("Acceptance evidence references must be nonempty strings.")
        serialized = _plan_json(plan)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        accepted = [r for r in self._records(plan.plan_id) if r.kind == "m3.orchestration.accepted"]
        if accepted:
            body = accepted[0].body
            if body["plan_digest"] != digest or body["accepted_by"] != accepted_by or body["evidence_refs"] != (evidence_refs or []):
                raise PlanStateTransitionError("Plan ID already binds different acceptance evidence.")
            return accepted[0].record_id
        now = datetime.datetime.now(datetime.timezone.utc)
        deadline = (now + datetime.timedelta(seconds=plan.bounds.timeout_seconds)).isoformat()
        record = self._append(plan, "accepted", "accepted", {
            "plan_json": serialized, "plan_digest": digest,
            "accepted_by": accepted_by, "evidence_refs": evidence_refs or [],
            "accepted_at": now.isoformat(), "deadline": deadline,
            "cost_budget": plan.bounds.cost_budget,
            "cost_accounting": "RESULT_REPORTED" if plan.bounds.cost_budget is not None else "NOT_CONFIGURED",
        })
        return record.record_id

    def recover(self, plan: Plan) -> tuple[dict[str, StepExecutionResult], str]:
        records = self._records(plan.plan_id)
        accepted = [r for r in records if r.kind == "m3.orchestration.accepted"]
        digest = hashlib.sha256(_plan_json(plan).encode("utf-8")).hexdigest()
        if len(accepted) != 1 or accepted[0].body.get("plan_digest") != digest:
            raise PlanStateTransitionError("Execution requires this exact explicitly accepted plan.")
        results: dict[str, StepExecutionResult] = {}
        for record in records:
            if record.kind in ("m3.orchestration.running", "m3.orchestration.result"):
                body = record.body
                if body.get("plan_digest") != digest:
                    raise PlanStateTransitionError("Step evidence binds another plan digest.")
                result = StepExecutionResult.from_dict(body["result"])
                results[result.step_id] = result
        return results, str(accepted[0].body["deadline"])

    def recover_cost(self, plan: Plan) -> float:
        """Total REPORTED cost over every committed attempt of this plan (all runs and resumes)."""
        seen: dict[tuple[str, int], float] = {}
        for record in self._records(plan.plan_id):
            if record.kind == "m3.orchestration.result":
                result = StepExecutionResult.from_dict(record.body["result"])
                if result.cost is not None:
                    seen[(result.step_id, result.attempt)] = result.cost
        return float(sum(seen.values()))

    def record_attempt(self, plan: Plan, result: StepExecutionResult, owner: str, *, running: bool) -> None:
        if not owner or result.step_id not in {s.step_id for s in plan.steps} or result.attempt < 1:
            raise PlanStateTransitionError("Attempt evidence must bind a valid plan step, attempt and owner.")
        if running and result.status != "MAY_HAVE_STARTED":
            raise PlanStateTransitionError("Pre-invocation evidence cannot assert a terminal outcome.")
        if not running and result.status not in ("COMPLETED", "FAILED", "MAY_HAVE_STARTED", "SKIPPED"):
            raise PlanStateTransitionError("Unsupported execution certainty.")
        if not running and result.status != "SKIPPED":
            prior = [r for r in self._records(plan.plan_id) if r.kind == "m3.orchestration.running"
                     and r.body["result"]["step_id"] == result.step_id
                     and r.body["result"]["attempt"] == result.attempt]
            if len(prior) != 1 or prior[0].body["owner"] != owner:
                raise PlanStateTransitionError("Terminal evidence requires its committed invocation owner.")
        self._append(plan, "running" if running else "result",
                     f"{result.step_id}:{result.attempt}:{'running' if running else 'result'}", {
            "plan_digest": hashlib.sha256(_plan_json(plan).encode("utf-8")).hexdigest(),
            "owner": owner, "certainty": "MAY_HAVE_STARTED" if running else result.status,
            "result": result.to_dict(),
        })
