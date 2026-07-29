"""Transport-independent values used by conversation entry points."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Modality = Literal["text", "voice", "attachment"]
LatencyPreference = Literal["quick", "balanced", "deep"]
RiskClass = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class ConversationRequest:
    text: str = ""
    modalities: tuple[Modality, ...] = ("text",)
    attachments: tuple[str, ...] = ()
    requested_output_format: str | None = None
    latency_preference: LatencyPreference = "balanced"
    risk_class: RiskClass = "low"
    session_id: str | None = None
    project_id: str | None = None
    tool_names: tuple[str, ...] = ()
    connector_names: tuple[str, ...] = ()
    preferred_route: str | None = None


@dataclass(frozen=True)
class ConversationContext:
    session_id: str
    project_id: str | None = None
    provider_availability: dict[str, bool] = field(default_factory=dict)
    connector_health: dict[str, bool] = field(default_factory=dict)
    estimated_latency_ms: dict[str, int] = field(default_factory=dict)
    estimated_cost_usd: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class RoutingDecision:
    route: str
    reason_code: str
    estimated_latency_ms: int | None = None
    estimated_cost_usd: float | None = None
    fallback_order: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "reason_code": self.reason_code,
            "estimated_latency_ms": self.estimated_latency_ms,
            "estimated_cost_usd": self.estimated_cost_usd,
            "fallback_order": list(self.fallback_order),
        }


@dataclass(frozen=True)
class ConversationEvent:
    id: int
    session_id: str
    type: str
    data: dict[str, Any] = field(default_factory=dict)
