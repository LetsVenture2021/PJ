"""Hierarchical kill switches and cooperative cancellation."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, Lock


@dataclass
class Cancellation:
    requested: Event = field(default_factory=Event)


class KillSwitches:
    def __init__(self) -> None:
        self._disabled: set[tuple[str, str]] = set()
        self._active: dict[str, Cancellation] = {}
        self._lock = Lock()

    def allowed(self, *, connector: str, project: str, workflow: str) -> bool:
        with self._lock:
            return not any(
                key in self._disabled
                for key in (
                    ("global", "*"),
                    ("connector", connector),
                    ("project", project),
                    ("workflow", workflow),
                )
            )

    def register(
        self, run_id: str, *, connector: str, project: str, workflow: str
    ) -> Cancellation | None:
        with self._lock:
            if any(
                key in self._disabled
                for key in (
                    ("global", "*"),
                    ("connector", connector),
                    ("project", project),
                    ("workflow", workflow),
                )
            ):
                return None
            token = Cancellation()
            self._active[run_id] = token
            return token

    def activate(self, scope: str, identifier: str = "*") -> None:
        if scope not in {"global", "connector", "project", "workflow"}:
            raise ValueError("invalid kill-switch scope")
        with self._lock:
            self._disabled.add((scope, identifier))
            # Cancellation is a request, never a claim that external effects rolled back.
            for token in self._active.values():
                token.requested.set()

    def finish(self, run_id: str) -> None:
        with self._lock:
            self._active.pop(run_id, None)
