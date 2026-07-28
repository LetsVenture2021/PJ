"""Shared contracts and infrastructure for operation domains."""

from .errors import (
    APIError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    ErrorMapping,
    NotFoundError,
    ServiceUnavailableError,
    UnprocessableError,
    UpstreamError,
    UpstreamTimeoutError,
    ValidationError,
    map_exception,
)
from .interfaces import ResponsesProvider, ToolDispatcher

__all__ = [
    "APIError",
    "AuthenticationError",
    "AuthorizationError",
    "ConflictError",
    "ErrorMapping",
    "NotFoundError",
    "ResponsesProvider",
    "ServiceUnavailableError",
    "ToolDispatcher",
    "UnprocessableError",
    "UpstreamError",
    "UpstreamTimeoutError",
    "ValidationError",
    "map_exception",
]
