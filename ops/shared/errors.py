"""Stable application error taxonomy and API-safe response mapping."""
from dataclasses import dataclass
from typing import Any, Callable


DetailFormatter = Callable[[Any], Any]


class APIError(Exception):
    """Base class for errors that may be exposed through an API boundary."""

    code = "internal_error"
    message = "An unexpected error occurred."
    status_code = 500

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        detail: Any = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.detail = detail
        super().__init__(self.message)


class ValidationError(APIError):
    code = "invalid_request"
    message = "The request is invalid."
    status_code = 400


class AuthenticationError(APIError):
    code = "authentication_required"
    message = "Authentication is required."
    status_code = 401


class AuthorizationError(APIError):
    code = "forbidden"
    message = "The request is not permitted."
    status_code = 403


class NotFoundError(APIError):
    code = "not_found"
    message = "The requested resource was not found."
    status_code = 404


class ConflictError(APIError):
    code = "conflict"
    message = "The request conflicts with the current resource state."
    status_code = 409


class UnprocessableError(APIError):
    code = "unprocessable_request"
    message = "The request could not be processed."
    status_code = 422


class UpstreamError(APIError):
    code = "upstream_error"
    message = "An upstream service request failed."
    status_code = 502


class ServiceUnavailableError(APIError):
    code = "service_unavailable"
    message = "The service is temporarily unavailable."
    status_code = 503


class UpstreamTimeoutError(UpstreamError):
    code = "upstream_timeout"
    message = "An upstream service timed out."
    status_code = 504


@dataclass(frozen=True)
class ErrorMapping:
    """Transport-neutral representation of an API error."""

    status_code: int
    payload: dict[str, Any]


def map_exception(
    exception: BaseException,
    request_id: str,
    *,
    detail_formatter: DetailFormatter | None = None,
) -> ErrorMapping:
    """Map an exception to a stable response without leaking unknown internals."""
    error = exception if isinstance(exception, APIError) else APIError()
    detail = error.detail
    if detail_formatter is not None:
        detail = detail_formatter(detail)
    return ErrorMapping(
        status_code=error.status_code,
        payload={
            "code": error.code,
            "message": error.message,
            "request_id": request_id,
            "detail": detail,
        },
    )

