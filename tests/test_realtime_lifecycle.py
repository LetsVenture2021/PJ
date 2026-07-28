import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import chatlog
import realtime_server


def obj(**values):
    return SimpleNamespace(**values)


def completed_stream(text="Done.", response_id="resp_final"):
    response = obj(
        id=response_id,
        output_text=text,
        output=[
            obj(
                type="message",
                content=[
                    obj(type="output_text", text=text, annotations=[]),
                ],
            ),
        ],
    )
    return [
        obj(type="response.output_text.delta", delta=text),
        obj(type="response.completed", response=response),
    ]


def failing_stream(message):
    def events():
        raise RuntimeError(message)
        yield

    return events()


class FakeResponses:
    def __init__(self, streams):
        self.streams = list(streams)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return iter(self.streams.pop(0))


class FakeOpenAIClient:
    def __init__(self, streams):
        self.responses = FakeResponses(streams)


class FakeSignalingResponse:
    status_code = 201
    text = "v=0\r\no=openai 1 1 IN IP4 127.0.0.1\r\n"


def parse_sse(response):
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.get_data(as_text=True).splitlines()
        if line.startswith("data: ")
    ]


class TestRealtimeSessionLifecycle(unittest.TestCase):
    """Integration baseline for the server/runtime/browser lifecycle.

    Retry contract: ``/session`` makes one upstream signaling request. The
    browser may make one ephemeral-token fallback for a retryable signaling
    response, but it has no retry loop. A failed Responses stream releases its
    turn lease; a caller may then start one fresh turn. Tests use no sleeps or
    timing races, so a retry always means one explicit second request.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = chatlog._DB_PATH
        chatlog._DB_PATH = Path(self.temp_dir.name) / "realtime.sqlite3"
        self.env = patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "test-openai-key",
                "PJ_TOOL_BRIDGE_TOKEN": "test-bridge-token",
            },
            clear=False,
        )
        self.env.start()
        self.prompt_perfecting = patch.object(
            realtime_server.promptops,
            "perfect_prompt",
            side_effect=lambda client, cfg, prompt, *, surface, required=True: {
                "original_prompt": prompt,
                "refined_prompt": prompt,
                "changed": False,
                "version": "test",
                "surface": surface,
                "intent_summary": "unchanged",
                "constraints_preserved": [],
                "original_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "refined_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            },
        )
        self.prompt_perfecting.start()
        realtime_server.app.config.update(TESTING=True)
        self.client = realtime_server.app.test_client()
        self.auth = {
            "Authorization": (
                f"Bearer {os.environ['PJ_TOOL_BRIDGE_TOKEN']}"
            ),
        }

    def tearDown(self):
        self.prompt_perfecting.stop()
        self.env.stop()
        chatlog._DB_PATH = self.old_db_path
        self.temp_dir.cleanup()

    def create_realtime_session(self):
        response = self.client.post(
            "/responses/sessions",
            json={"title": "Realtime lifecycle", "channel": "realtime"},
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()["session"]["id"]

    def assert_structured_error(self, response, *, status, code, request_id):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.headers["x-request-id"], request_id)
        self.assertEqual(
            set(response.get_json()),
            {"ok", "error"},
        )
        error = response.get_json()["error"]
        self.assertFalse(response.get_json()["ok"])
        self.assertEqual(
            set(error),
            {"code", "message", "request_id", "detail"},
        )
        self.assertEqual(error["code"], code)
        self.assertEqual(error["request_id"], request_id)
        return error

    def test_connect_start_stream_and_resume_success(self):
        session_id = self.create_realtime_session()
        with patch.object(
            realtime_server.requests,
            "post",
            return_value=FakeSignalingResponse(),
        ) as signaling:
            connected = self.client.post(
                (
                    f"/session?session_id={session_id}"
                    "&voice_mode=full_power"
                ),
                data="v=0\r\no=browser 1 1 IN IP4 127.0.0.1\r\n",
                content_type="application/sdp",
                headers={"x-pj-client-request-id": "connect-request"},
            )

        self.assertEqual(connected.status_code, 201)
        self.assertEqual(connected.mimetype, "application/sdp")
        self.assertEqual(
            connected.headers["x-pj-session-id"],
            session_id,
        )
        self.assertEqual(
            connected.headers["x-request-id"],
            "connect-request",
        )
        signaling.assert_called_once()
        call = signaling.call_args
        self.assertEqual(
            call.args[0],
            "https://api.openai.com/v1/realtime/calls",
        )
        self.assertEqual(call.kwargs["timeout"], 35)
        self.assertEqual(
            call.kwargs["files"]["sdp"][1],
            "v=0\r\no=browser 1 1 IN IP4 127.0.0.1",
        )
        session_config = json.loads(
            call.kwargs["files"]["session"][1],
        )
        self.assertEqual(session_config["type"], "realtime")
        self.assertFalse(
            session_config["audio"]["input"]["turn_detection"][
                "create_response"
            ],
        )

        runtime_client = FakeOpenAIClient(
            [completed_stream("Lifecycle complete.")],
        )
        with patch.object(
            realtime_server,
            "OPENAI_CLIENT_FACTORY",
            return_value=runtime_client,
        ):
            streamed = self.client.post(
                f"/responses/sessions/{session_id}/turns",
                json={"message": "Run the lifecycle"},
                headers={
                    **self.auth,
                    "x-pj-client-request-id": "stream-request",
                },
                buffered=True,
            )

        self.assertEqual(streamed.status_code, 200)
        self.assertEqual(streamed.mimetype, "text/event-stream")
        events = parse_sse(streamed)
        self.assertEqual(
            [event["type"] for event in events],
            ["session", "prompt.perfected", "text.delta", "completion"],
        )
        self.assertEqual(events[0]["session_id"], session_id)
        self.assertEqual(events[0]["request_id"], "stream-request")
        self.assertEqual(events[-1]["text"], "Lifecycle complete.")
        self.assertEqual(events[-1]["session_id"], session_id)

        resumed = self.client.post(
            f"/responses/sessions/{session_id}/resume",
            json={},
            headers=self.auth,
        )
        history = resumed.get_json()["session"]["history"]
        self.assertEqual(
            [(item["role"], item["content"]) for item in history],
            [
                ("user", "Run the lifecycle"),
                ("assistant", "Lifecycle complete."),
            ],
        )

    def test_signaling_timeout_is_structured_and_not_retried(self):
        session_id = self.create_realtime_session()
        with patch.object(
            realtime_server.requests,
            "post",
            side_effect=realtime_server.requests.Timeout(
                "provider took too long",
            ),
        ) as signaling:
            response = self.client.post(
                f"/session?session_id={session_id}",
                data="v=0\r\n",
                content_type="application/sdp",
                headers={"x-pj-client-request-id": "timeout-request"},
            )

        error = self.assert_structured_error(
            response,
            status=504,
            code="openai_timeout",
            request_id="timeout-request",
        )
        self.assertEqual(
            error["message"],
            "OpenAI realtime signaling timed out.",
        )
        self.assertIn("provider took too long", error["detail"])
        signaling.assert_called_once()

    def test_stream_error_is_structured_and_turn_can_be_retried(self):
        session_id = self.create_realtime_session()
        runtime_client = FakeOpenAIClient(
            [
                failing_stream("transient provider disconnect"),
                completed_stream("Retry completed.", "resp_retry"),
            ],
        )

        with patch.object(
            realtime_server,
            "OPENAI_CLIENT_FACTORY",
            return_value=runtime_client,
        ):
            failed = self.client.post(
                f"/responses/sessions/{session_id}/turns",
                json={"message": "First attempt"},
                headers={
                    **self.auth,
                    "x-pj-client-request-id": "failed-stream-request",
                },
                buffered=True,
            )
            retried = self.client.post(
                f"/responses/sessions/{session_id}/turns",
                json={"message": "Explicit retry"},
                headers={
                    **self.auth,
                    "x-pj-client-request-id": "retry-stream-request",
                },
                buffered=True,
            )

        failed_events = parse_sse(failed)
        self.assertEqual(
            [event["type"] for event in failed_events],
            ["session", "prompt.perfected", "error"],
        )
        error = failed_events[-1]["error"]
        self.assertEqual(
            set(error),
            {"code", "message", "request_id", "detail"},
        )
        self.assertEqual(error["code"], "responses_turn_failed")
        self.assertEqual(error["message"], "Responses turn failed.")
        self.assertEqual(
            error["request_id"],
            "failed-stream-request",
        )
        self.assertIn("transient provider disconnect", error["detail"])

        retry_events = parse_sse(retried)
        self.assertEqual(retry_events[-1]["type"], "completion")
        self.assertEqual(retry_events[-1]["text"], "Retry completed.")
        self.assertEqual(len(runtime_client.responses.calls), 2)

    def test_webrtc_client_contract_bounds_retry_and_closes_resources(self):
        source = (
            Path(__file__).resolve().parents[1] / "webrtc_client.html"
        ).read_text(encoding="utf-8")
        start = source[
            source.index("async function startSession()"):
            source.index("function stopSession(")
        ]
        stop = source[
            source.index("function stopSession("):
            source.index("async function sendTextMessage()")
        ]
        send = source[
            source.index("async function sendTextMessage()"):
            source.index("async function refreshFullPowerSessions()")
        ]

        self.assertIn(
            'state.pc.createDataChannel("oai-events")',
            start,
        )
        self.assertEqual(
            start.count("negotiateWithEphemeralToken("),
            1,
        )
        self.assertNotIn("while (", start)
        self.assertNotIn("setTimeout(", start)

        self.assertIn("state.dataChannel.close()", stop)
        self.assertIn("state.pc.close()", stop)
        self.assertIn("track.stop()", stop)
        self.assertIn("state.dataChannel = null", stop)
        self.assertIn("state.pc = null", stop)
        self.assertIn("state.micStream = null", stop)
        self.assertNotIn("state.activeSessionId = null", stop)

        item_create = send.index('type: "conversation.item.create"')
        response_create = send.index('type: "response.create"')
        self.assertLess(item_create, response_create)


if __name__ == "__main__":
    unittest.main()
