import io
import json
import logging
import tempfile
import unittest
from pathlib import Path

import promptops
from ops.shared.errors import (
    APIError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    UnprocessableError,
    UpstreamError,
    UpstreamTimeoutError,
    ValidationError,
    map_exception,
)
from ops.shared.io import atomic_copy, sha256_file, write_json_atomic
from ops.shared.logging import JsonFormatter, log_context, redact_sensitive
from ops.shared.providers.openai import OpenAIRealtimeProvider
from ops.shared.retry import RetryPolicy, get_with_retry


class _Response:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text
        self.headers = {}
        self.closed = False

    def close(self):
        self.closed = True


class _HttpProvider:
    request_errors = (OSError,)
    timeout_errors = (TimeoutError,)

    def __init__(self, responses):
        self.responses = list(responses)

    def get(self, _url, **_kwargs):
        result = self.responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class OpsSharedTestCase(unittest.TestCase):
    def test_error_taxonomy_has_stable_status_and_codes(self):
        cases = [
            (ValidationError(), 400, "invalid_request"),
            (AuthenticationError(), 401, "authentication_required"),
            (AuthorizationError(), 403, "forbidden"),
            (NotFoundError(), 404, "not_found"),
            (ConflictError(), 409, "conflict"),
            (UnprocessableError(), 422, "unprocessable_request"),
            (UpstreamError(), 502, "upstream_error"),
            (ServiceUnavailableError(), 503, "service_unavailable"),
            (UpstreamTimeoutError(), 504, "upstream_timeout"),
        ]

        for error, status, code in cases:
            with self.subTest(error=type(error).__name__):
                mapped = map_exception(error, "request-123")
                self.assertEqual(mapped.status_code, status)
                self.assertEqual(mapped.payload["code"], code)
                self.assertEqual(mapped.payload["request_id"], "request-123")

    def test_unknown_exception_maps_to_api_safe_internal_error(self):
        mapped = map_exception(RuntimeError("database password leaked"), "request-123")

        self.assertEqual(mapped.status_code, 500)
        self.assertEqual(mapped.payload["code"], "internal_error")
        self.assertEqual(mapped.payload["message"], "An unexpected error occurred.")
        self.assertIsNone(mapped.payload["detail"])

    def test_custom_api_error_preserves_stable_contract(self):
        mapped = map_exception(
            APIError(
                "A turn is already active.",
                code="session_turn_in_progress",
                status_code=409,
                detail="lease held",
            ),
            "request-123",
            detail_formatter=str.upper,
        )

        self.assertEqual(mapped.status_code, 409)
        self.assertEqual(
            mapped.payload,
            {
                "code": "session_turn_in_progress",
                "message": "A turn is already active.",
                "request_id": "request-123",
                "detail": "LEASE HELD",
            },
        )
    def test_structured_logging_includes_context_and_redacts_secrets(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logger = logging.getLogger("test.pj.structured")
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)

        with log_context(request_id="req-123", session_id="session-456"):
            logger.info(
                "tool.execution.completed",
                extra={
                    "authorization": "Bearer header-secret",
                    "detail": "api_key=embedded-secret",
                    "metadata": {
                        "access_token": "nested-secret",
                        "tool_name": "get_current_time",
                    },
                },
            )

        entry = json.loads(stream.getvalue())
        self.assertEqual(entry["message"], "tool.execution.completed")
        self.assertEqual(entry["request_id"], "req-123")
        self.assertEqual(entry["session_id"], "session-456")
        self.assertEqual(entry["authorization"], "[REDACTED]")
        self.assertEqual(entry["metadata"]["access_token"], "[REDACTED]")
        self.assertEqual(entry["metadata"]["tool_name"], "get_current_time")
        self.assertNotIn("header-secret", stream.getvalue())
        self.assertNotIn("embedded-secret", stream.getvalue())
        self.assertNotIn("nested-secret", stream.getvalue())

    def test_sensitive_redaction_covers_nested_values_and_token_strings(self):
        redacted = redact_sensitive(
            {
                "safe": "visible",
                "client_secret": "top-secret",
                "nested": {
                    "password": "hunter2",
                    "message": "Authorization: Bearer abc.def",
                    "provider_key": "sk-abcdefghijk12345",
                    "repr": "{'refresh_token': 'message-value'}",
                    "quoted": "password='multi word value'",
                },
            }
        )

        self.assertEqual(redacted["safe"], "visible")
        self.assertEqual(redacted["client_secret"], "[REDACTED]")
        serialized = json.dumps(redacted)
        self.assertNotIn("top-secret", serialized)
        self.assertNotIn("hunter2", serialized)
        self.assertNotIn("abc.def", serialized)
        self.assertNotIn("message-value", serialized)
        self.assertNotIn("multi word value", serialized)
        self.assertNotIn("sk-abcdefghijk12345", serialized)
    def test_realtime_provider_uses_supplied_credentials(self):
        class Http:
            def __init__(self):
                self.calls = []

            def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return _Response(200)

        http = Http()
        provider = OpenAIRealtimeProvider(http)
        provider.mint_client_secret("test-key", {"model": "realtime"}, timeout=10)

        self.assertEqual(
            http.calls[0][1]["headers"]["Authorization"],
            "Bearer test-key",
        )

    def test_retry_closes_failed_response_and_uses_backoff(self):
        failed = _Response(503, "unavailable")
        ready = _Response(200, "ok")
        delays = []

        result = get_with_retry(
            _HttpProvider([failed, ready]),
            "https://provider.example/resource",
            policy=RetryPolicy(attempts=2, backoff_seconds=0.25),
            sleep=delays.append,
        )

        self.assertIs(result, ready)
        self.assertTrue(failed.closed)
        self.assertEqual(delays, [0.25])

    def test_non_retryable_response_fails_immediately(self):
        with self.assertRaisesRegex(RuntimeError, "HTTP 400"):
            get_with_retry(
                _HttpProvider([_Response(400, "bad request")]),
                "https://provider.example/resource",
                policy=RetryPolicy(attempts=4),
                sleep=lambda _delay: self.fail("must not retry"),
            )

    def test_prompting_accepts_provider_interface(self):
        class Provider:
            def create_response(self, **_kwargs):
                return {
                    "output_text": json.dumps(
                        {
                            "refined_prompt": "Prepare the report.",
                            "intent_summary": "Prepare a report.",
                            "constraints_preserved": [],
                        }
                    )
                }

        result = promptops.perfect_prompt(
            None,
            {
                "model": "test-model",
                "prompt_perfecting": {
                    "enabled": True,
                    "surfaces": ["cli"],
                },
            },
            "Prepare report.",
            surface="cli",
            provider=Provider(),
        )

        self.assertEqual(result["refined_prompt"], "Prepare the report.")

    def test_atomic_io_helpers_preserve_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.bin"
            copied = root / "nested" / "copied.bin"
            manifest = root / "manifest.json"
            source.write_bytes(b"operation-boundary")

            atomic_copy(source, copied)
            write_json_atomic(manifest, {"sha256": sha256_file(copied)})

            self.assertEqual(copied.read_bytes(), source.read_bytes())
            self.assertEqual(
                json.loads(manifest.read_text())["sha256"],
                sha256_file(source),
            )


if __name__ == "__main__":
    unittest.main()
