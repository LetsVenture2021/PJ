"""Application service and stable API boundary for durable jobs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import JobHandler, JobState, TERMINAL_STATES
from .repository import JobRepository


class JobService:
    def __init__(self, repository: JobRepository, handlers: Mapping[str, JobHandler]):
        self.repository, self.handlers = repository, handlers

    def create(
        self, handler_name: str, payload: Mapping[str, Any], budget_units: int
    ) -> dict[str, Any]:
        if handler_name not in self.handlers:
            raise ValueError("unknown job handler")
        handler = self.handlers[handler_name]
        clean = handler.validate(payload)
        estimate = handler.estimate(clean)
        if estimate.cost_units > budget_units:
            raise ValueError("job estimate exceeds budget")
        job_id = self.repository.create_job(
            handler_name, clean, budget_units, list(handler.plan(clean))
        )
        return self.repository.get_job(job_id) or {}

    def pause(self, job_id: str) -> bool:
        return self.repository.transition(
            job_id, {JobState.QUEUED, JobState.LEASED, JobState.RUNNING}, JobState.PAUSED
        )

    def resume(self, job_id: str) -> bool:
        return self.repository.transition(
            job_id, {JobState.PAUSED, JobState.WAITING_APPROVAL}, JobState.QUEUED
        )

    def cancel(self, job_id: str) -> bool:
        job = self.repository.get_job(job_id)
        if not job or job["state"] in TERMINAL_STATES:
            return False
        target = (
            JobState.CANCELLING
            if job["state"] in {JobState.LEASED, JobState.RUNNING}
            else JobState.CANCELLED
        )
        return self.repository.transition(job_id, {job["state"]}, target)

    def retry(self, job_id: str) -> bool:
        return self.repository.transition(
            job_id, {JobState.FAILED, JobState.PARTIALLY_SUCCEEDED}, JobState.QUEUED
        )


def create_jobs_blueprint(service_getter: Any) -> Any:
    """Create Flask routes without constructing state or threads at import time."""
    from flask import Blueprint, jsonify, request

    blueprint = Blueprint("jobs", __name__, url_prefix="/jobs")

    def public_job(job: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in job.items() if key != "input"}

    @blueprint.post("")
    def create_job() -> Any:
        body = request.get_json(silent=True) or {}
        try:
            job = service_getter().create(
                str(body.get("handler", "")),
                body.get("input") or {},
                int(body.get("budget_units", 0)),
            )
        except (TypeError, ValueError) as exc:
            return jsonify({"error": {"code": "invalid_job", "message": str(exc)}}), 400
        return jsonify(public_job(job)), 201

    @blueprint.get("/<job_id>")
    def status(job_id: str) -> Any:
        job = service_getter().repository.get_job(job_id)
        return (
            (jsonify(public_job(job)), 200)
            if job
            else (jsonify({"error": {"code": "job_not_found"}}), 404)
        )

    @blueprint.get("/<job_id>/events")
    def events(job_id: str) -> Any:
        return jsonify(
            {
                "events": service_getter().repository.events(
                    job_id, int(request.args.get("after", 0))
                )
            }
        )

    @blueprint.get("/schedules")
    def schedules() -> Any:
        return jsonify({"schedules": service_getter().repository.schedules()})

    @blueprint.put("/schedules/<schedule_id>")
    def put_schedule(schedule_id: str) -> Any:
        body = request.get_json(silent=True) or {}
        kind = body.get("kind")
        if kind not in {"cron", "one_time"}:
            return jsonify({"error": {"code": "invalid_schedule"}}), 400
        try:
            service_getter().repository.put_schedule(
                schedule_id,
                str(body["handler"]),
                kind,
                body.get("config") or {},
                str(body["timezone"]),
                str(body.get("missed_run_policy", "skip")),
                int(body["budget_units"]),
                quiet_hours=body.get("quiet_hours"),
            )
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": {"code": "invalid_schedule"}}), 400
        return jsonify({"id": schedule_id}), 200

    @blueprint.delete("/schedules/<schedule_id>")
    def delete_schedule(schedule_id: str) -> Any:
        return (
            ("", 204)
            if service_getter().repository.delete_schedule(schedule_id)
            else (jsonify({"error": {"code": "schedule_not_found"}}), 404)
        )

    def action(name: str, job_id: str) -> Any:
        changed = getattr(service_getter(), name)(job_id)
        return (
            (jsonify({"status": name}), 202)
            if changed
            else (jsonify({"error": {"code": "invalid_job_state"}}), 409)
        )

    for name in ("pause", "resume", "cancel", "retry"):
        blueprint.add_url_rule(
            f"/<job_id>/{name}",
            f"job_{name}",
            lambda job_id, action_name=name: action(action_name, job_id),
            methods=["POST"],
        )
    return blueprint
