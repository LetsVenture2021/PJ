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
import skills
import voice


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
                "require_approval": "never",
                "headers": {"Authorization": "Bearer ${READY_TOKEN}"},
            },
            {
                "label": "missing",
                "url": "https://missing.test/mcp",
                "enabled": True,
                "require_approval": "never",
                "headers": {"Authorization": "Bearer ${MISSING_TOKEN}"},
            },
            {
                "label": "off",
                "url": "https://off.test/mcp",
                "enabled": False,
                "require_approval": "never",
            },
        ]
        manifest = responses_runtime.capability_manifest(
            self.cfg,
            mcp_servers=servers,
            environ={"READY_TOKEN": "super-secret-value"},
        )

        self.assertEqual(
            manifest["local_functions"]["count"], len(skills.TOOL_SCHEMAS)
        )
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
                "require_approval": "never",
                "headers": {"Authorization": "Bearer ${TOKEN}"},
            }],
            environ={"TOKEN": "abc123"},
        )
        mcp = next(tool for tool in tools if tool["type"] == "mcp")
        self.assertEqual(mcp["headers"]["Authorization"], "Bearer abc123")
        names = {tool.get("name") for tool in tools}
        self.assertNotIn("delegate_advanced_task", names)

    def test_approval_required_mcp_uses_explicit_owner_flow(self):
        server = {
            "label": "protected",
            "url": "https://protected.test/mcp",
            "enabled": True,
            "require_approval": "always",
        }
        tools = responses_runtime.build_tools(
            self.cfg, mcp_servers=[server], environ={}
        )
        self.assertTrue(any(tool.get("type") == "mcp" for tool in tools))
        manifest = responses_runtime.capability_manifest(
            self.cfg, mcp_servers=[server], environ={}
        )
        self.assertEqual(manifest["mcp_servers"][0]["status"], "configured")
        self.assertTrue(manifest["mcp_servers"][0]["runtime_enabled"])
        self.assertEqual(
            manifest["mcp_servers"][0]["approval_flow"],
            "explicit_owner_confirmation",
        )

    def test_realtime_keeps_direct_tools_and_adds_delegation_only_there(self):
        session = realtime_config.realtime_session_config()
        self.assertEqual(session["model"], realtime_config.REALTIME_MODEL)
        names = {tool["name"] for tool in session["tools"]}
        self.assertTrue(
            realtime_config.REALTIME_EXCLUDED_TOOL_NAMES.isdisjoint(names)
        )
        self.assertEqual(
            session["tools"][-1]["name"], "delegate_advanced_task"
        )
        self.assertEqual(
            realtime_server._function_tool_schemas(), session["tools"]
        )

    def test_document_tool_emits_artifact_before_public_result(self):
        function_response = obj(
            id="resp_document",
            output_text="",
            output=[obj(
                type="function_call",
                name="draft_document",
                call_id="call_document",
                arguments=json.dumps({
                    "template": "meeting_memo",
                    "title": "Download",
                    "sections_json": "{}",
                }),
            )],
        )
        client = FakeClient([
            [obj(type="response.completed", response=function_response)],
            final_stream("Document ready.", response_id="resp_done"),
        ])
        artifact = {
            "artifact_id": "ART-" + ("a" * 32),
            "doc_id": "DOC-test",
            "version": 1,
            "format": "md",
            "filename": "document.md",
            "mime_type": "text/markdown; charset=utf-8",
            "byte_size": 12,
            "sha256": "b" * 64,
            "status": "ready",
            "audience_ready": False,
            "download_url": "/responses/artifacts/ART-" + ("a" * 32),
        }
        orchestrator = responses_runtime.ResponsesOrchestrator(
            client,
            self.cfg,
            dispatcher=lambda _name, _arguments: {
                "status": "draft",
                "path": "/private/document.md",
                "artifact": artifact,
            },
        )
        with patch.object(
            responses_runtime,
            "_verified_artifact_from_result",
            return_value=artifact,
        ):
            events = list(orchestrator.stream_turn("Create a document"))

        event_types = [event["type"] for event in events]
        self.assertLess(
            event_types.index("artifact.ready"),
            event_types.index("tool.result"),
        )
        tool_result = next(
            event for event in events if event["type"] == "tool.result"
        )
        self.assertNotIn("path", tool_result["result"])
        continuation_output = json.loads(
            client.responses.calls[1]["input"][0]["output"]
        )
        self.assertNotIn("path", continuation_output)
        self.assertEqual(events[-1]["artifacts"], [artifact])

    def test_path_redaction_preserves_repository_relative_paths(self):
        result = responses_runtime.redact_server_paths({
            "matches": [
                {"path": "src/example.py", "line": 3},
                {"path": "/private/project/secret.py", "line": 4},
            ],
            "output_path": "reports/result.json",
            "source_path": r"C:\private\source.py",
        })
        self.assertEqual(result["matches"][0]["path"], "src/example.py")
        self.assertNotIn("path", result["matches"][1])
        self.assertEqual(result["output_path"], "reports/result.json")
        self.assertNotIn("source_path", result)
        embedded = responses_runtime.redact_server_paths({
            "artifact_error": (
                "copy failed: /Users/private/document.md; "
                "see https://example.test/docs/path"
            ),
        })
        self.assertNotIn("/Users/private/document.md", embedded["artifact_error"])
        self.assertIn("[server path redacted]", embedded["artifact_error"])
        self.assertIn(
            "https://example.test/docs/path", embedded["artifact_error"]
        )
        download_url = "/responses/artifacts/ART-" + ("c" * 32)
        self.assertEqual(
            responses_runtime.redact_server_paths(download_url),
            download_url,
        )

    def test_terminal_voice_sanitizes_tool_results(self):
        with (
            patch.object(
                voice,
                "dispatch_realtime_function",
                return_value={
                    "path": "/Users/private/document.md",
                    "artifact": {
                        "download_url":
                            "/responses/artifacts/ART-" + ("d" * 32),
                    },
                },
            ),
            patch("builtins.print"),
        ):
            output = json.loads(
                voice._run_tool_call("draft_document", "{}")
            )
        self.assertNotIn("path", output)
        self.assertEqual(
            output["artifact"]["download_url"],
            "/responses/artifacts/ART-" + ("d" * 32),
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
        self.assertEqual(
            client.responses.calls[0]["instructions"], "Test instructions"
        )
        self.assertEqual(
            client.responses.calls[1]["instructions"], "Test instructions"
        )
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

    def test_mcp_approval_request_pauses_without_provider_id_exposure(self):
        response = obj(
            id="resp_mcp_pending",
            output_text="",
            output=[obj(
                type="mcp_approval_request",
                id="mcp_provider_approval",
                server_label="github",
                name="create_issue",
                arguments='{"title":"Test"}',
            )],
        )
        client = FakeClient([[
            obj(type="response.completed", response=response)
        ]])
        events = list(
            responses_runtime.ResponsesOrchestrator(
                client, self.cfg
            ).stream_turn("Create an issue")
        )
        approval = events[-1]
        self.assertEqual(approval["type"], "approval.required")
        self.assertEqual(approval["approval_kind"], "mcp")
        self.assertEqual(approval["arguments"], {"title": "Test"})
        self.assertEqual(approval["_response_id"], "resp_mcp_pending")
        self.assertEqual(
            approval["_provider_item_id"], "mcp_provider_approval"
        )

    def test_local_policy_approval_pauses_before_dispatch(self):
        response = obj(
            id="resp_local_pending",
            output_text="",
            output=[obj(
                type="function_call",
                call_id="call_approved",
                name="approve_codeops_task",
                arguments='{"task_id":"task","approval_evidence":"owner"}',
            )],
        )
        client = FakeClient([[
            obj(type="response.completed", response=response)
        ]])
        dispatched = []
        events = list(
            responses_runtime.ResponsesOrchestrator(
                client,
                self.cfg,
                dispatcher=lambda name, arguments: dispatched.append(
                    (name, arguments)
                ),
            ).stream_turn("Approve it")
        )
        self.assertEqual(events[-1]["type"], "approval.required")
        self.assertEqual(events[-1]["approval_kind"], "local_function")
        self.assertEqual(dispatched, [])

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

    def test_mcp_approval_is_opaque_bound_and_resumable(self):
        session = chatlog.new_session(channel="web")
        pending_response = obj(
            id="resp_mcp_pending",
            output_text="",
            output=[obj(
                type="mcp_approval_request",
                id="mcp_provider_approval",
                server_label="github",
                name="create_issue",
                arguments='{"title":"Owner approved"}',
            )],
        )
        first_client = FakeClient([[
            obj(type="response.completed", response=pending_response)
        ]])
        with patch.object(
            realtime_server,
            "OPENAI_CLIENT_FACTORY",
            return_value=first_client,
        ):
            turn = self.client.post(
                f"/responses/sessions/{session['id']}/turns",
                json={"message": "Create the issue"},
                headers=self.auth,
                buffered=True,
            )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in turn.get_data(as_text=True).splitlines()
            if line.startswith("data: ")
        ]
        approval = next(
            event for event in events
            if event["type"] == "approval.required"
        )
        self.assertNotIn("resp_mcp_pending", turn.get_data(as_text=True))
        self.assertNotIn("mcp_provider_approval", turn.get_data(as_text=True))
        self.assertGreaterEqual(len(approval["approval_id"]), 32)

        blocked = self.client.post(
            f"/responses/sessions/{session['id']}/turns",
            json={"message": "Start another turn"},
            headers=self.auth,
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(
            blocked.get_json()["error"]["code"],
            "session_approval_pending",
        )

        continuation_client = FakeClient([
            final_stream("The approved MCP action completed.")
        ])
        with patch.object(
            realtime_server,
            "OPENAI_CLIENT_FACTORY",
            return_value=continuation_client,
        ):
            resolved = self.client.post(
                (
                    f"/responses/sessions/{session['id']}/approvals/"
                    f"{approval['approval_id']}"
                ),
                json={"approve": True},
                headers=self.auth,
                buffered=True,
            )
        self.assertEqual(resolved.status_code, 200)
        resolved_body = resolved.get_data(as_text=True)
        self.assertIn("event: approval.resolved", resolved_body)
        self.assertIn("event: completion", resolved_body)
        call = continuation_client.responses.calls[0]
        self.assertEqual(call["previous_response_id"], "resp_mcp_pending")
        self.assertEqual(call["input"], [{
            "type": "mcp_approval_response",
            "approval_request_id": "mcp_provider_approval",
            "approve": True,
        }])

        detail = chatlog.session_detail(session["id"])
        self.assertEqual(detail["pending_approvals"], [])
        self.assertEqual(
            [item["role"] for item in detail["history"]],
            ["user", "assistant"],
        )

    def test_local_approval_executes_only_after_trusted_resolution(self):
        session = chatlog.new_session(channel="web")
        pending_response = obj(
            id="resp_local_pending",
            output_text="",
            output=[obj(
                type="function_call",
                call_id="call_local_approval",
                name="approve_codeops_task",
                arguments=(
                    '{"task_id":"task-123",'
                    '"approval_evidence":"owner click"}'
                ),
            )],
        )
        with patch.object(
            realtime_server,
            "OPENAI_CLIENT_FACTORY",
            return_value=FakeClient([[
                obj(type="response.completed", response=pending_response)
            ]]),
        ):
            turn = self.client.post(
                f"/responses/sessions/{session['id']}/turns",
                json={"message": "Approve the task"},
                headers=self.auth,
                buffered=True,
            )
        approval = next(
            json.loads(line.removeprefix("data: "))
            for line in turn.get_data(as_text=True).splitlines()
            if line.startswith("data: ")
            and '"type": "approval.required"' in line
        )

        continuation_client = FakeClient([
            final_stream("The owner-approved local action completed.")
        ])
        with (
            patch.object(
                realtime_server,
                "OPENAI_CLIENT_FACTORY",
                return_value=continuation_client,
            ),
            patch.object(
                realtime_server.skills,
                "dispatch",
                return_value={"approval_state": "approved"},
            ) as dispatch,
        ):
            resolved = self.client.post(
                (
                    f"/responses/sessions/{session['id']}/approvals/"
                    f"{approval['approval_id']}"
                ),
                json={"approve": True},
                headers=self.auth,
                buffered=True,
            )
        self.assertEqual(resolved.status_code, 200)
        dispatch.assert_called_once_with(
            "approve_codeops_task",
            {
                "task_id": "task-123",
                "approval_evidence": "owner click",
            },
            approval_granted=True,
        )
        continuation = continuation_client.responses.calls[0]
        self.assertEqual(
            continuation["input"][0]["call_id"],
            "call_local_approval",
        )
        self.assertEqual(
            json.loads(continuation["input"][0]["output"]),
            {"approval_state": "approved"},
        )

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
        self.assertEqual(
            capabilities["local_functions"]["count"],
            len(skills.TOOL_SCHEMAS),
        )
        self.assertNotIn("headers", json.dumps(capabilities))


if __name__ == "__main__":
    unittest.main()
