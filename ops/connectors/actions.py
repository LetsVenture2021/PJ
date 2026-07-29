"""Action lifecycle contract and an idempotent orchestration implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping
from uuid import uuid4

from .models import ActionPreview, ActionReceipt


class ActionError(RuntimeError):
    pass


class ApprovalRequired(ActionError):
    pass


class ConnectorAction(ABC):
    @abstractmethod
    def validate(self, arguments: Mapping[str, Any]) -> None: ...
    @abstractmethod
    def preview(self, arguments: Mapping[str, Any]) -> ActionPreview: ...
    @abstractmethod
    def execute(self, arguments: Mapping[str, Any], idempotency_key: str) -> str | None: ...
    @abstractmethod
    def verify(self, external_reference: str | None) -> bool: ...

    def compensate(self, receipt: ActionReceipt) -> bool:
        raise ActionError("This action does not support compensation")


class ActionRunner:
    def __init__(self) -> None:
        self._receipts: dict[str, ActionReceipt] = {}

    def run(
        self,
        connector_id: str,
        action_name: str,
        action: ConnectorAction,
        arguments: Mapping[str, Any],
        *,
        idempotency_key: str,
        approved: bool = False,
    ) -> ActionReceipt:
        if not idempotency_key:
            raise ActionError("An idempotency key is required")
        if idempotency_key in self._receipts:
            return self._receipts[idempotency_key]
        action.validate(arguments)
        preview = action.preview(arguments)
        if preview.approval_required and not approved:
            raise ApprovalRequired("Explicit approval is required after preview")
        reference = action.execute(arguments, idempotency_key)
        verified = action.verify(reference)
        receipt = ActionReceipt(
            str(uuid4()),
            connector_id,
            action_name,
            idempotency_key,
            "succeeded" if verified else "unknown",
            reference,
            verified,
        )
        self._receipts[idempotency_key] = receipt
        return receipt
