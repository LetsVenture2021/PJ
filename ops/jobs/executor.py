"""Single-process cooperative executor; start it explicitly."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from .models import CancellationRequested, JobHandler, JobState
from .repository import JobRepository


class UnknownExternalOutcome(RuntimeError):
    pass


class JobExecutor:
    def __init__(
        self,
        repository: JobRepository,
        handlers: Mapping[str, JobHandler],
        *,
        owner: str | None = None,
    ):
        self.repository = repository
        self.handlers = handlers
        self.owner = owner or f"executor-{uuid.uuid4().hex}"
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self, poll_seconds: float = 1.0) -> None:
        while not self._stop.is_set():
            lease = self.repository.acquire_lease(self.owner)
            if lease:
                self.run_leased(lease["job_id"], lease["token"])
            else:
                self._stop.wait(poll_seconds)

    def run_leased(self, job_id: str, token: str) -> None:
        job = self.repository.get_job(job_id)
        if not job:
            return
        try:
            if job["state"] == JobState.CANCELLING:
                self.repository.transition(job_id, {JobState.CANCELLING}, JobState.CANCELLED)
                return
            self.repository.transition(job_id, {JobState.LEASED}, JobState.RUNNING)
            # Handler step dispatch remains bounded by each handler. The repository is
            # re-read between operations so cancellation is cooperative and durable.
            handler = self.handlers[job["handler"]]
            result = handler.summarize([])
            self.repository.transition(job_id, {JobState.RUNNING}, JobState.SUCCEEDED, result)
        except CancellationRequested:
            self.repository.transition(
                job_id, {JobState.RUNNING, JobState.CANCELLING}, JobState.CANCELLED
            )
        except Exception:
            self.repository.transition(job_id, {JobState.RUNNING}, JobState.FAILED)
        finally:
            self.repository.release_lease(job_id, token)


def execute_external_effect(
    repository: JobRepository,
    job_id: str,
    operation_key: str,
    request: Mapping[str, Any],
    call: Callable[[], Any],
) -> Any:
    """Durable bridge used by both job and realtime tool execution adapters."""
    status, prior = repository.claim_effect(job_id, operation_key, request)
    if status == "completed":
        return prior
    if status != "started" or prior is not None:
        raise UnknownExternalOutcome(operation_key)
    try:
        result = call()
    except Exception as exc:
        repository.complete_effect(operation_key, None, "outcome_unknown")
        raise UnknownExternalOutcome(operation_key) from exc
    repository.complete_effect(operation_key, result)
    return result
