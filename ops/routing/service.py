"""Capability routing without provider identifiers in customer-facing modes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Capabilities:
    quality: int
    latency: str
    cost: float
    modalities: frozenset[str]
    context_size: int
    tools: frozenset[str]
    private: bool = False


@dataclass(frozen=True)
class RouteRequest:
    mode: str
    modality: str = "text"
    context_size: int = 0
    required_tools: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RouteDecision:
    provider: str
    model_profile: str
    decision_code: str
    estimated_cost: float

    def audit_metadata(self) -> dict[str, str | float]:
        return {"decision_code": self.decision_code, "estimated_cost": self.estimated_cost}


class Router:
    def __init__(
        self,
        capabilities: Mapping[tuple[str, str], Capabilities],
        modes: Mapping[str, Mapping[str, object]],
    ):
        self.capabilities = capabilities
        self.modes = modes

    def route(self, request: RouteRequest) -> RouteDecision:
        policy = self.modes.get(request.mode)
        if policy is None:
            raise ValueError("unknown_execution_mode")
        require_private = policy.get("privacy") == "local"
        candidates = []
        for (provider, profile), capability in self.capabilities.items():
            if require_private and not capability.private:
                continue
            if (
                request.modality not in capability.modalities
                or request.context_size > capability.context_size
            ):
                continue
            if not request.required_tools <= capability.tools:
                continue
            candidates.append((provider, profile, capability))
        if not candidates:
            raise RuntimeError(
                "private_fallback_refused" if require_private else "no_capable_provider"
            )
        candidates.sort(key=lambda item: (-item[2].quality, item[2].cost, item[0], item[1]))
        provider, profile, capability = candidates[0]
        return RouteDecision(provider, profile, "ROUTE_CAPABILITY_MATCH", capability.cost)
