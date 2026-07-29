"""Bounded retry policy for provider I/O."""

import time
from dataclasses import dataclass
from typing import Any

from .interfaces import HttpProvider, HttpResponse
from .logging import get_logger


_LOGGER = get_logger("retry")


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 4
    backoff_seconds: float = 0.5
    max_delay_seconds: float = 30.0

    def normalized_attempts(self) -> int:
        return max(1, int(self.attempts or 1))

    def delay_for(self, attempt: int, response: HttpResponse | None = None) -> float:
        """Return a bounded delay, preferring valid provider guidance."""
        maximum = max(0.0, float(self.max_delay_seconds))
        delay = min(maximum, max(0.0, float(self.backoff_seconds)) * (2**attempt))
        if response is None:
            return delay
        retry_after = response.headers.get("Retry-After")
        if retry_after is None:
            retry_after = response.headers.get("retry-after")
        if retry_after is None:
            return delay
        try:
            guided_delay = float(retry_after)
        except (TypeError, ValueError):
            return delay
        if guided_delay < 0:
            return delay
        return min(maximum, guided_delay)


def get_with_retry(
    provider: HttpProvider, url: str, *, policy: RetryPolicy, sleep=time.sleep, **kwargs: Any
) -> HttpResponse:
    attempts = policy.normalized_attempts()
    last_error = ""
    for attempt in range(attempts):
        failed_response = None
        try:
            response = provider.get(url, **kwargs)
        except provider.request_errors as exc:
            last_error = str(exc)
        else:
            if response.status_code < 400:
                return response
            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            if response.status_code != 429 and response.status_code < 500:
                # A response owns a pooled connection until its body is consumed
                # or it is closed.  This branch is not retried, but it still has
                # to release that connection before surfacing the status error.
                response.close()
                raise RuntimeError(last_error)
            failed_response = response
            response.close()
        if attempt + 1 < attempts:
            delay = policy.delay_for(attempt, failed_response)
            _LOGGER.debug("Retrying provider request in %.3f seconds", delay)
            sleep(delay)
    raise RuntimeError(f"request failed after {attempts} attempts: {last_error}")
