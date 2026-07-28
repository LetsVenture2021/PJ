import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import huggingface_mcp_server as hf  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class TestHuggingFaceMcpServer(unittest.TestCase):
    def test_initialize(self):
        response = hf.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(response["result"]["serverInfo"]["name"], "pj-hugging-face")
        self.assertIn("tools", response["result"]["capabilities"])

    def test_tools_list(self):
        response = hf.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertIn("hf_search_models", names)
        self.assertIn("hf_inference", names)

    @patch("urllib.request.urlopen")
    def test_search_models_is_bounded(self, urlopen):
        urlopen.return_value = FakeResponse([{"id": "org/model"}])
        result = hf.call_tool("hf_search_models", {"query": "embed", "limit": 999})
        self.assertEqual(result[0]["id"], "org/model")
        request = urlopen.call_args.args[0]
        self.assertIn("limit=50", request.full_url)

    def test_inference_requires_token(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "HF_TOKEN"):
                hf.call_tool("hf_inference", {"model_id": "org/model", "inputs": "hello"})

    @patch("urllib.request.urlopen")
    def test_inference_uses_bearer_token_without_returning_it(self, urlopen):
        urlopen.return_value = FakeResponse([{"generated_text": "hello"}])
        with patch.dict(os.environ, {"HF_TOKEN": "hf_test_secret"}, clear=True):
            result = hf.call_tool(
                "hf_inference",
                {"model_id": "org/model", "inputs": "hi", "parameters": {"max_new_tokens": 8}},
            )
        request = urlopen.call_args.args[0]
        self.assertEqual(request.headers["Authorization"], "Bearer hf_test_secret")
        self.assertNotIn("hf_test_secret", json.dumps(result))

    def test_notification_has_no_response(self):
        self.assertIsNone(hf.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_stdio_round_trip(self):
        incoming = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\n")
        outgoing = io.StringIO()
        with patch.object(sys, "stdin", incoming), patch.object(sys, "stdout", outgoing):
            hf.serve()
        self.assertEqual(json.loads(outgoing.getvalue())["id"], 7)


if __name__ == "__main__":
    unittest.main()
