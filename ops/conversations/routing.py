"""Deterministic, metadata-only capability routing."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Mapping

from .models import ConversationContext, ConversationRequest, RoutingDecision


class Route(StrEnum):
    REALTIME = "realtime"
    RESPONSES = "responses"
    LOCAL = "local"
    HOSTED = "hosted"
    DELEGATED = "delegated"


class RoutingError(ValueError):
    """A request cannot be served within configured safety or budget limits."""

    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


LONG_RUNNING_TOOLS = frozenset(
    {
        "run_codex_task",
        "sync_vector_store",
        "learn_from_vector_store",
        "run_codeops_validation",
        "deploy_generated_site",
    }
)
STRUCTURED_FORMATS = frozenset({"json", "json_schema", "structured"})


class CapabilityRouter:
    """Choose a route without examining or emitting user content."""

    def __init__(self, settings: Any, tool_policy: Mapping[str, Any]):
        self.settings = settings
        self.policy = tool_policy

    def _get(self, name: str, default: Any) -> Any:
        return getattr(
            self.settings,
            name,
            self.settings.get(name, default) if isinstance(self.settings, dict) else default,
        )

    def choose(self, request: ConversationRequest, context: ConversationContext) -> RoutingDecision:
        enabled = self._get("enabled_routes", ())
        enabled = tuple(str(item) for item in enabled)
        available = context.provider_availability
        candidates = [route for route in enabled if available.get(route, True)]
        if not candidates:
            raise RoutingError("no_provider_available")

        unhealthy = [
            name
            for name in request.connector_names
            if not context.connector_health.get(name, False)
        ]
        if unhealthy:
            raise RoutingError("required_connector_unhealthy")

        policy_tools = self.policy.get("tools", {})
        approval = any(
            policy_tools.get(name, self.policy.get("default")) == "approval"
            for name in request.tool_names
        )
        long_running = any(name in LONG_RUNNING_TOOLS for name in request.tool_names)
        structured = (request.requested_output_format or "").lower() in STRUCTURED_FORMATS

        if request.preferred_route:
            preferred = request.preferred_route.lower()
            if preferred not in candidates:
                raise RoutingError("preferred_route_unavailable")
            if preferred == Route.REALTIME and long_running:
                route, reason = Route.DELEGATED, "long_running_work_requires_delegation"
            elif preferred == Route.REALTIME and approval:
                route, reason = Route.RESPONSES, "approval_sensitive_requires_responses"
            elif preferred == Route.REALTIME and structured:
                route, reason = Route.RESPONSES, "structured_output_requires_responses"
            else:
                route, reason = preferred, "explicit_user_preference"
        elif long_running:
            route, reason = Route.DELEGATED, "long_running_work_requires_delegation"
        elif approval:
            route, reason = Route.RESPONSES, "approval_sensitive_requires_responses"
        elif structured:
            route, reason = Route.RESPONSES, "structured_output_requires_responses"
        elif "voice" in request.modalities:
            route, reason = Route.REALTIME, "voice_requires_realtime"
        elif request.attachments or "attachment" in request.modalities:
            route, reason = Route.RESPONSES, "attachments_require_responses"
        elif request.tool_names and all(name.startswith("local_") for name in request.tool_names):
            route, reason = Route.LOCAL, "deterministic_local_tool_selected"
        elif request.tool_names:
            route, reason = Route.HOSTED, "hosted_tool_selected"
        else:
            route, reason = Route.RESPONSES, "text_defaults_to_responses"

        route = str(route)
        if route not in candidates:
            safe = [item for item in self._get("safe_fallback_order", ()) if item in candidates]
            if not safe:
                raise RoutingError("required_provider_unavailable")
            route, reason = safe[0], "provider_unavailable_safe_fallback"
        cost = context.estimated_cost_usd.get(route, 0.0)
        if cost > float(self._get("maximum_estimated_spend_usd", 0.0)):
            raise RoutingError("estimated_spend_exceeds_limit")
        latency = context.estimated_latency_ms.get(route)
        budget = self._get("timeout_budgets_ms", {}).get(route)
        if budget is not None and latency is not None and latency > budget:
            alternatives = [
                r
                for r in self._get("safe_fallback_order", ())
                if r in candidates
                and context.estimated_latency_ms.get(r, 0)
                <= self._get("timeout_budgets_ms", {}).get(r, 10**12)
            ]
            if alternatives:
                route, reason = alternatives[0], "latency_budget_safe_fallback"
                latency, cost = (
                    context.estimated_latency_ms.get(route),
                    context.estimated_cost_usd.get(route, 0.0),
                )
            else:
                raise RoutingError("estimated_latency_exceeds_budget")
        return RoutingDecision(
            route, reason, latency, cost, tuple(self._get("safe_fallback_order", ()))
        )

    route = choose
