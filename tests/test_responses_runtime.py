import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import chatlog
import realtime_config
import realtime_server
import responses_runtime


def obj(**values):
    return SimpleNamespace(**values)


class FakeResponses:
    def __init__(self, streams):
        self.streams = list(streams)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return iter(self.streams.pop(0))


class FakeClient:
    def __init__(self, streams):
        self.responses = FakeResponses(streams)


def final_stream(text="Done.", response_id="resp_final", annotations=None):
    content = [
        obj(
            type="output_text",
            text=text,
            annotations=annotations or [],
        )
    ]
    response = obj(
        id=response_id,
        output_text=text,
        output=[obj(type="message", content=content)],
    )
    return [
        obj(type="response.output_text.delta", delta=text),
        obj(type="response.completed", response=response),
    ]


class TestResponsesRuntime(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "name": "PJ",
            "model": "test-model",
            "instructions": "Test instructions",
            "instructions_file": "pj_instructions.txt",
            "instructions_source": "pj_instructions.txt",
            "vector_store_id": "vs_test",
            "web_search_enabled": True,
            "tool_search_enabled": True,
            "code_interpreter_enabled": False,
            "image_generation_enabled": False,
            "computer_use_enabled": True,
        }

    def test_manifest_reports_all_states_without_secret_values(self):
        servers = [
            {
                "label": "ready",
                "url": "https://user:pass@ready.test/mcp?token=url-secret",
                "enabled": True,
                "headers": {"Authorization": "Bearer ${READY_TOKEN}"},
            },
            {
                "label": "missing",
                "url": "https://missing.test/mcp",
                "enabled": True,
                "headers": {"Authorization": "Bearer ${MISSING_TOKEN}"},
            },
            {
                "label": "off",
                "url": "https://off.test/mcp",
                "enabled": False,
            },
        ]
        manifest = responses_runtime.capability_manifest(
            self.cfg,
            mcp_servers=servers,
            environ={"READY_TOKEN": "super-secret-value"},
        )

        self.assertEqual(manifest["local_functions"]["count"], 52)
        self.assertEqual(
            [server["status"] for server in manifest["mcp_servers"]],
            ["configured", "degraded", "disabled"],
        )
        self.assertEqual(
            manifest["native"]["computer_use"]["status"], "unavailable"
        )
        rendered = json.dumps(manifest)
        self.assertNotIn("super-secret-value", rendered)
        self.assertNotIn("READY_TOKEN", rendered)
        self.assertNotIn("MISSING_TOKEN", rendered)
        self.assertNotIn("url-secret", rendered)
        self.assertNotIn("user:pass", rendered)

    def test_tool_assembly_expands_mcp_secret_but_excludes_delegation(self):
        tools = responses_runtime.build_tools(
            self.cfg,
            mcp_servers=[{
                "label": "secure",
                "url": "https://secure.test/mcp",
                "enabled": True,
                "headers": {"Authorization": "Bearer ${TOKEN}"},
            }],
            environ={"TOKEN": "abc123"},
        )
        mcp = next(tool for tool in tools if tool["type"] == "mcp")
        self.assertEqual(mcp["headers"]["Authorization"], "Bearer abc123")
        names = {tool.get("name") for tool in tools}
        self.assertNotIn("delegate_advanced_task", names)

    def test_realtime_keeps_direct_tools_and_adds_delegation_only_there(self):
        session = realtime_config.realtime_session_config()
        self.assertEqual(session["model"], realtime_config.REALTIME_MODEL)
        self.assertEqual(len(session["tools"]), 53)
        self.assertEqual(
            session["tools"][-1]["name"], "delegate_advanced_task"
        )

    def test_recursive_local_tool_turn_streams_typed_events_and_continuity(self):
        function_response = obj(
            id="resp_tool",
            output=[
                obj(
                    type="function_call",
                    name="get_current_time",
                    call_id="call_1",
                    arguments="{}",
                )
            ],
        )
        citation = obj(
            type="url_citation",
            title="Source",
            url="https://example.test/source",
            start_index=0,
            end_index=4,
        )
        completed_stream = final_stream("Done.", annotations=[citation])
        completed_stream[-1].response.output.append(
            obj(
                type="file_search_call",
                id="file_call_1",
                status="completed",
                results=[
                    obj(
                        file_id="file_1",
                        filename="brief.txt",
                        score=0.9,
                        text="Relevant source excerpt",
                    )
                ],
            )
        )
        client = FakeClient([
            [
                obj(
                    type="response.function_call_arguments.delta",
                    call_id="call_1",
                    delta="{}",
                ),
                obj(type="response.completed", response=function_response),
            ],
            completed_stream,
        ])
        dispatched = []

        def dispatch(name, arguments):
            dispatched.append((name, arguments))
            return {"iso8601": "2026-01-01T00:00:00Z"}

        events = list(
            responses_runtime.ResponsesOrchestrator(
                client, self.cfg, dispatcher=dispatch
            ).stream_turn("What time is it?", previous_response_id="resp_previous")
        )

        self.assertEqual(dispatched, [("get_current_time", {})])
        self.assertEqual(
            client.responses.calls[0]["previous_response_id"], "resp_previous"
        )
        self.assertEqual(
            client.responses.calls[1]["previous_response_id"], "resp_tool"
        )
        self.assertNotIn("instructions", client.responses.calls[1])
        self.assertIn("tool.call", [event["type"] for event in events])
        self.assertIn("tool.result", [event["type"] for event in events])
        self.assertIn("citation", [event["type"] for event in events])
        self.assertIn("source", [event["type"] for event in events])
        completion = events[-1]
        self.assertEqual(completion["type"], "completion")
        self.assertEqual(completion["_response_id"], "resp_final")
        self.assertEqual(completion["sources"][0]["filename"], "brief.txt")

    def test_structured_output_is_parsed(self):
        client = FakeClient([final_stream('{"answer":"ok"}')])
        events = list(
            responses_runtime.ResponsesOrchestrator(
                client, self.cfg
            ).stream_turn(
                "Answer",
                text_format={
                    "type": "json_schema",
                    "name": "answer",
                    "schema": {"type": "object"},
                    "strict": True,
                },
            )
        )
        self.assertEqual(events[-1]["structured_output"], {"answer": "ok"})

    def test_advanced_delegation_returns_summary_and_citations(self):
        citation = obj(
            type="url_citation",
            title="Evidence",
            url="https://example.test/evidence",
        )
        client = FakeClient([
            final_stream(
                "A detailed delegated answer.",
                annotations=[citation],
            )
        ])
        result = responses_runtime.delegate_advanced_task(
            "Research this", client=client, cfg=self.cfg
        )
        self.assertEqual(result["summary"], "A detailed delegated answer.")
        self.assertEqual(
            result["details"]["citations"][0]["title"], "Evidence"
        )

    def test_advanced_delegation_rejects_recursion(self):
        token = responses_runtime._delegation_active.set(True)
        try:
            result = responses_runtime.delegate_advanced_task(
                "Nested", client=FakeClient([]), cfg=self.cfg
            )
        finally:
            responses_runtime._delegation_active.reset(token)
        self.assertIn("Recursive", result["error"])

    def test_advanced_delegation_bounds_detailed_voice_payload(self):
        client = FakeClient([final_stream("x" * 7000)])
        result = responses_runtime.delegate_advanced_task(
            "Research this", client=client, cfg=self.cfg
        )
        self.assertEqual(
            len(result["details"]["text"]),
            responses_runtime.MAX_DELEGATION_DETAIL_LENGTH,
        )
        self.assertTrue(result["details"]["text_truncated"])


class TestResponsesRoutes(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = chatlog._DB_PATH
        chatlog._DB_PATH = Path(self.temp_dir.name) / "test.sqlite3"
        self.env = patch.dict(
            os.environ,
            {
                "PJ_TOOL_BRIDGE_TOKEN": "bridge-secret",
                "OPENAI_API_KEY": "test-openai-key",
            },
            clear=False,
        )
        self.env.start()
        realtime_server.app.config.update(TESTING=True)
        self.client = realtime_server.app.test_client()
        self.auth = {"Authorization": "Bearer bridge-secret"}

    def tearDown(self):
        self.env.stop()
        chatlog._DB_PATH = self.old_db_path
        self.temp_dir.cleanup()

    def test_routes_require_bridge_auth(self):
        response = self.client.get("/responses/capabilities")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.get_json()["error"]["code"], "bridge_auth_required"
        )

    def test_responses_routes_fail_closed_without_bridge_token(self):
        with patch.dict(os.environ, {"PJ_TOOL_BRIDGE_TOKEN": ""}):
            response = self.client.get("/responses/capabilities")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "bridge_auth_not_configured",
        )

    def test_session_lifecycle_stream_and_server_side_response_id(self):
        created = self.client.post(
            "/responses/sessions",
            json={"title": "Web test"},
            headers=self.auth,
        )
        self.assertEqual(created.status_code, 201)
        session_id = created.get_json()["session"]["id"]
        self.assertGreaterEqual(len(session_id), 32)

        fake_client = FakeClient([final_stream("Hello from PJ.")])
        with patch.object(
            realtime_server,
            "OPENAI_CLIENT_FACTORY",
            return_value=fake_client,
        ):
            turn = self.client.post(
                f"/responses/sessions/{session_id}/turns",
                json={"message": "Hello"},
                headers=self.auth,
                buffered=True,
            )

        self.assertEqual(turn.status_code, 200)
        self.assertEqual(turn.mimetype, "text/event-stream")
        body = turn.get_data(as_text=True)
        self.assertIn("event: text.delta", body)
        self.assertIn("event: completion", body)
        self.assertNotIn("resp_final", body)

        resumed = self.client.post(
            f"/responses/sessions/{session_id}/resume",
            json={},
            headers=self.auth,
        )
        detail = resumed.get_json()["session"]
        self.assertEqual([item["role"] for item in detail["history"]],
                         ["user", "assistant"])
        self.assertNotIn("last_response_id", detail)

        search = self.client.get(
            "/responses/sessions/search?q=Hello",
            headers=self.auth,
        )
        self.assertGreaterEqual(search.get_json()["count"], 1)
        listed = self.client.get(
            "/responses/sessions?limit=10",
            headers=self.auth,
        )
        self.assertEqual(listed.get_json()["sessions"][0]["id"], session_id)

        stored = chatlog.get_session(session_id)
        self.assertEqual(stored["last_response_id"], "resp_final")

    def test_turn_rejects_browser_supplied_response_id(self):
        session = chatlog.new_session(channel="web")
        response = self.client.post(
            f"/responses/sessions/{session['id']}/turns",
            json={"message": "Hi", "previous_response_id": "untrusted"},
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"]["code"], "invalid_request_body"
        )

    def test_concurrent_turn_for_same_session_is_rejected(self):
        session = chatlog.new_session(channel="web")
        token = chatlog.claim_session_turn(session["id"])
        try:
            response = self.client.post(
                f"/responses/sessions/{session['id']}/turns",
                json={"message": "Overlapping turn"},
                headers=self.auth,
            )
        finally:
            chatlog.release_session_turn(session["id"], token)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "session_turn_in_progress",
        )

    def test_structured_output_contract(self):
        session = chatlog.new_session(channel="web")
        fake_client = FakeClient([final_stream('{"answer":"ok"}')])
        with patch.object(
            realtime_server,
            "OPENAI_CLIENT_FACTORY",
            return_value=fake_client,
        ):
            response = self.client.post(
                f"/responses/sessions/{session['id']}/turns",
                json={
                    "message": "Answer",
                    "structured_output": {
                        "name": "answer",
                        "schema": {
                            "type": "object",
                            "properties": {"answer": {"type": "string"}},
                            "required": ["answer"],
                            "additionalProperties": False,
                        },
                        "strict": True,
                    },
                },
                headers=self.auth,
                buffered=True,
            )
        body = response.get_data(as_text=True)
        self.assertIn('"structured_output": {"answer": "ok"}', body)
        self.assertEqual(
            fake_client.responses.calls[0]["text"]["format"]["name"], "answer"
        )

    def test_capability_contract_reports_direct_function_count(self):
        response = self.client.get(
            "/responses/capabilities", headers=self.auth
        )
        self.assertEqual(response.status_code, 200)
        capabilities = response.get_json()["capabilities"]
        self.assertEqual(capabilities["local_functions"]["count"], 52)
        self.assertNotIn("headers", json.dumps(capabilities))


if __name__ == "__main__":
    unittest.main()
