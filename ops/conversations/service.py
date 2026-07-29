"""Conversation lifecycle service with a resumable in-process event journal."""

from __future__ import annotations

import threading
import uuid

from .models import ConversationEvent


class ConversationService:
    def __init__(self) -> None:
        self._events: dict[str, list[ConversationEvent]] = {}
        self._lock = threading.Lock()

    def create(self, conversation_id: str | None = None) -> str:
        conversation_id = conversation_id or uuid.uuid4().hex
        with self._lock:
            self._events.setdefault(conversation_id, [])
        return conversation_id

    def exists(self, conversation_id: str) -> bool:
        return conversation_id in self._events

    def publish(
        self, conversation_id: str, event_type: str, data: dict | None = None
    ) -> ConversationEvent:
        with self._lock:
            journal = self._events[conversation_id]
            event = ConversationEvent(len(journal) + 1, conversation_id, event_type, data or {})
            journal.append(event)
            return event

    def events(self, conversation_id: str, after: int = 0) -> tuple[ConversationEvent, ...]:
        return tuple(event for event in self._events[conversation_id] if event.id > after)
