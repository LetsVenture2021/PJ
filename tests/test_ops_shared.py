import json
import tempfile
import unittest
from pathlib import Path

import promptops
from ops.shared.io import atomic_copy, sha256_file, write_json_atomic
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
                    "output_text": json.dumps({
                        "refined_prompt": "Prepare the report.",
                        "intent_summary": "Prepare a report.",
                        "constraints_preserved": [],
                    })
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
