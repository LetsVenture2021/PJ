import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import chatlog
import docops
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

        self.assertEqual(manifest["local_functions"]["count"], len(skills.TOOL_SCHEMAS))
        self.assertEqual(
            [server["status"] for server in manifest["mcp_servers"]],
            ["configured", "degraded", "disabled"],
        )
        self.assertEqual(manifest["native"]["computer_use"]["status"], "unavailable")
        rendered = json.dumps(manifest)
        self.assertNotIn("super-secret-value", rendered)
        self.assertNotIn("READY_TOKEN", rendered)
        self.assertNotIn("MISSING_TOKEN", rendered)
        self.assertNotIn("url-secret", rendered)
        self.assertNotIn("user:pass", rendered)

    def test_plural_instruction_and_vector_config_preserves_legacy_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.txt").write_text("One")
            (root / "two.txt").write_text("Two")
            (root / "config.json").write_text(
                json.dumps(
                    {
                        "name": "PJ",
                        "model": "test-model",
                        "instruction_files": ["one.txt", "two.txt"],
                        "instructions_file": "one.txt",
                        "vector_store_ids": ["vs_one", "vs_two"],
                        "vector_store_id": "vs_one",
                    }
                )
            )
            cfg = responses_runtime.load_config(root)
            tools = responses_runtime.build_tools(cfg, mcp_servers=[])

        self.assertEqual(cfg["instructions"], "One\n\nTwo")
        self.assertEqual(cfg["instructions_source"], "one.txt")
        file_search = next(tool for tool in tools if tool["type"] == "file_search")
        self.assertEqual(file_search["vector_store_ids"], ["vs_one", "vs_two"])

    def test_tool_assembly_expands_mcp_secret_but_excludes_delegation(self):
        tools = responses_runtime.build_tools(
            self.cfg,
            mcp_servers=[
                {
                    "label": "secure",
                    "url": "https://secure.test/mcp",
                    "enabled": True,
                    "require_approval": "never",
                    "headers": {"Authorization": "Bearer ${TOKEN}"},
                }
            ],
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
        tools = responses_runtime.build_tools(self.cfg, mcp_servers=[server], environ={})
        self.assertTrue(any(tool.get("type") == "mcp" for tool in tools))
        manifest = responses_runtime.capability_manifest(self.cfg, mcp_servers=[server], environ={})
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
        self.assertTrue(realtime_config.REALTIME_EXCLUDED_TOOL_NAMES.isdisjoint(names))
        self.assertEqual(session["tools"][-1]["name"], "delegate_advanced_task")
        self.assertEqual(realtime_server._function_tool_schemas(), session["tools"])
        with self.assertRaisesRegex(ValueError, "not available"):
            responses_runtime.dispatch_realtime_function(
                "generate_image_asset",
                {"prompt": "blocked", "idempotency_key": "blocked"},
            )
        full_power = realtime_config.realtime_session_config(voice_mode="full_power")
        self.assertTrue(session["audio"]["input"]["turn_detection"]["create_response"])
        self.assertFalse(full_power["audio"]["input"]["turn_detection"]["create_response"])
        self.assertTrue(full_power["audio"]["input"]["turn_detection"]["interrupt_response"])

    def test_rejected_continuation_starts_a_fresh_thread(self):
        client = FakeClient([final_stream("Recovered.", response_id="resp_recovered")])
        original_create = client.responses.create
        rejected = []

        class ContinuationRejected(Exception):
            status_code = 500

        def flaky_create(**kwargs):
            if kwargs.get("previous_response_id") and not rejected:
                rejected.append(kwargs["previous_response_id"])
                raise ContinuationRejected()
            return original_create(**kwargs)

        client.responses.create = flaky_create
        orchestrator = responses_runtime.ResponsesOrchestrator(
            client,
            self.cfg,
            dispatcher=lambda _name, _arguments: {},
        )
        events = list(orchestrator.stream_turn("hello", previous_response_id="resp_poisoned"))

        self.assertEqual(rejected, ["resp_poisoned"])
        self.assertNotIn("previous_response_id", client.responses.calls[-1])
        self.assertTrue(events)

    def test_document_tool_emits_artifact_before_public_result(self):
        function_response = obj(
            id="resp_document",
            output_text="",
            output=[
                obj(
                    type="function_call",
                    name="draft_document",
                    call_id="call_document",
                    arguments=json.dumps(
                        {
                            "template": "meeting_memo",
                            "title": "Download",
                            "sections_json": "{}",
                        }
                    ),
                )
            ],
        )
        client = FakeClient(
            [
                [obj(type="response.completed", response=function_response)],
                final_stream("Document ready.", response_id="resp_done"),
            ]
        )
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
        tool_result = next(event for event in events if event["type"] == "tool.result")
        self.assertNotIn("path", tool_result["result"])
        continuation_output = json.loads(client.responses.calls[1]["input"][0]["output"])
        self.assertNotIn("path", continuation_output)
        self.assertEqual(events[-1]["artifacts"], [artifact])

    def test_path_redaction_preserves_repository_relative_paths(self):
        result = responses_runtime.redact_server_paths(
            {
                "matches": [
                    {"path": "src/example.py", "line": 3},
                    {"path": "/private/project/secret.py", "line": 4},
                ],
                "output_path": "reports/result.json",
                "source_path": r"C:\private\source.py",
            }
        )
        self.assertEqual(result["matches"][0]["path"], "src/example.py")
        self.assertNotIn("path", result["matches"][1])
        self.assertEqual(result["output_path"], "reports/result.json")
        self.assertNotIn("source_path", result)
        embedded = responses_runtime.redact_server_paths(
            {
                "artifact_error": (
                    "copy failed: /Users/private/document.md; see https://example.test/docs/path"
                ),
            }
        )
        self.assertNotIn("/Users/private/document.md", embedded["artifact_error"])
        self.assertIn("[server path redacted]", embedded["artifact_error"])
        self.assertIn("https://example.test/docs/path", embedded["artifact_error"])
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
                        "download_url": "/responses/artifacts/ART-" + ("d" * 32),
                    },
                },
            ),
            patch("builtins.print"),
        ):
            output = json.loads(voice._run_tool_call("draft_document", "{}"))
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
            url="https://user:password@example.test/source?token=secret#fragment",
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
        client = FakeClient(
            [
                [
                    obj(
                        type="response.function_call_arguments.delta",
                        call_id="call_1",
                        delta="{}",
                    ),
                    obj(type="response.completed", response=function_response),
                ],
                completed_stream,
            ]
        )
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
        self.assertEqual(client.responses.calls[0]["previous_response_id"], "resp_previous")
        self.assertEqual(client.responses.calls[1]["previous_response_id"], "resp_tool")
        self.assertEqual(client.responses.calls[0]["instructions"], "Test instructions")
        self.assertEqual(client.responses.calls[1]["instructions"], "Test instructions")
        self.assertIn("tool.call", [event["type"] for event in events])
        self.assertIn("tool.result", [event["type"] for event in events])
        self.assertIn("citation", [event["type"] for event in events])
        self.assertIn("source", [event["type"] for event in events])
        self.assertEqual(
            len(
                [
                    event
                    for event in events
                    if event["type"] == "tool.call" and event.get("call_id") == "call_1"
                ]
            ),
            1,
        )
        self.assertEqual(
            len(
                [
                    event
                    for event in events
                    if event["type"] == "tool.result" and event.get("call_id") == "call_1"
                ]
            ),
            1,
        )
        self.assertEqual(
            len(
                [
                    event
                    for event in events
                    if event["type"] == "tool.call" and event.get("call_id") == "file_call_1"
                ]
            ),
            1,
        )
        self.assertEqual(
            len(
                [
                    event
                    for event in events
                    if event["type"] == "tool.result" and event.get("call_id") == "file_call_1"
                ]
            ),
            1,
        )
        completion = events[-1]
        self.assertEqual(completion["type"], "completion")
        self.assertEqual(completion["_response_id"], "resp_final")
        self.assertEqual(completion["sources"][0]["filename"], "brief.txt")
        self.assertEqual(
            completion["citations"][0]["url"],
            "https://example.test/source",
        )
        self.assertNotIn("secret", json.dumps(events))

    def test_structured_output_is_parsed(self):
        client = FakeClient([final_stream('{"answer":"ok"}')])
        events = list(
            responses_runtime.ResponsesOrchestrator(client, self.cfg).stream_turn(
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

    def test_native_tool_stream_emits_one_rich_final_result(self):
        final = obj(
            id="resp_native",
            output_text="Done.",
            output=[
                obj(
                    type="mcp_call",
                    id="mcp_call_1",
                    server_label="github",
                    name="search_code",
                    status="completed",
                    output='{"matches":3}',
                )
            ],
        )
        client = FakeClient(
            [
                [
                    obj(
                        type="response.mcp_call.completed",
                        item_id="mcp_call_1",
                        server_label="github",
                        name="search_code",
                    ),
                    obj(type="response.completed", response=final),
                ]
            ]
        )
        events = list(
            responses_runtime.ResponsesOrchestrator(client, self.cfg).stream_turn("Search code")
        )
        results = [
            event
            for event in events
            if event["type"] == "tool.result" and event.get("call_id") == "mcp_call_1"
        ]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["output"], '{"matches":3}')

    def test_send_message_prints_native_tool_results_without_local_result_field(self):
        final = obj(
            id="resp_native",
            output_text="Done.",
            output=[
                obj(
                    type="mcp_call",
                    id="mcp_call_1",
                    server_label="codex",
                    name="run_codex_task",
                    status="completed",
                )
            ],
        )
        client = FakeClient(
            [
                [
                    obj(
                        type="response.mcp_call.completed",
                        item_id="mcp_call_1",
                        server_label="codex",
                        name="run_codex_task",
                    ),
                    obj(type="response.completed", response=final),
                ]
            ]
        )
        state = {"previous_response_id": None}
        output = io.StringIO()

        with patch.object(responses_runtime, "save_state"), redirect_stdout(output):
            reply = responses_runtime.send_message(client, self.cfg, state, "Run Codex")

        self.assertEqual(reply, "Done.")
        self.assertEqual(state["previous_response_id"], "resp_native")
        self.assertIn('✅ {"status": "completed"}', output.getvalue())

    def test_server_paths_are_redacted_when_embedded_in_messages(self):
        value = {
            "error": (
                "file missing: /Users/alice/private/deck.pptx; "
                "cache: /srv/pj/private/export.bin; "
                r"drive: C:\PJ\private\deck.pptx; "
                r"share: \\server\private\deck.pptx"
            ),
            "nested": ["open file:///Users/alice/private/source.md"],
            "documentation": ("see https://docs.n8n.io/integrations/builtin/core-nodes/"),
            "download_url": ("/responses/artifacts/ART-0123456789abcdef0123456789abcdef"),
            "near_miss_download_url": (
                "/responses/artifacts/ART-0123456789abcdef0123456789abcdef/extra"
            ),
            "source_path": "relative/source.md",
            "output_path": r"C:\private\source.py",
        }
        redacted = responses_runtime.redact_server_paths(value)
        serialized = json.dumps(redacted)
        self.assertIn("file missing:", redacted["error"])
        self.assertNotIn("/Users/", serialized)
        self.assertNotIn("/srv/pj/", serialized)
        self.assertNotIn(r"C:\\PJ", serialized)
        self.assertNotIn(r"\\server\private", redacted["error"])
        self.assertNotIn("file://", serialized)
        # A path-like field whose value is not itself an absolute server
        # path is repository-relative evidence: it is kept (not silently
        # dropped), matching a client-safe result.
        self.assertEqual(redacted["source_path"], "relative/source.md")
        # A path-like field whose value IS an absolute server path is
        # dropped outright rather than merely text-redacted in place.
        self.assertNotIn("output_path", redacted)
        self.assertEqual(
            redacted["documentation"],
            "see https://docs.n8n.io/integrations/builtin/core-nodes/",
        )
        self.assertEqual(
            redacted["download_url"],
            "/responses/artifacts/ART-0123456789abcdef0123456789abcdef",
        )
        self.assertIn(
            "[server path redacted]",
            redacted["near_miss_download_url"],
        )

    def test_powerpoint_completion_is_blocked_after_one_repair(self):
        client = FakeClient(
            [
                final_stream("Premature completion.", response_id="resp_first"),
                final_stream("Still no file.", response_id="resp_second"),
            ]
        )
        events = list(
            responses_runtime.ResponsesOrchestrator(client, self.cfg).stream_turn(
                "Create a PowerPoint presentation"
            )
        )

        self.assertEqual(len(client.responses.calls), 2)
        self.assertEqual(events[-2]["type"], "text.delta")
        self.assertEqual(events[-1]["type"], "completion")
        self.assertEqual(
            events[-1]["deliverable"],
            {"status": "incomplete", "requested_format": "pptx"},
        )
        self.assertTrue(any(event["type"] == "deliverable.incomplete" for event in events))
        rendered = json.dumps(events)
        self.assertNotIn("Premature completion", rendered)
        self.assertNotIn("Still no file", rendered)

    def test_validated_powerpoint_artifact_enables_completion_and_redacts_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_db = docops._DB_PATH
            old_docs = docops.DOCS_DIR
            old_exports = docops.EXPORTS_DIR
            try:
                docops._DB_PATH = root / "docops.sqlite3"
                docops.DOCS_DIR = root / "documents"
                docops.DOCS_DIR.mkdir()
                docops.EXPORTS_DIR = docops.DOCS_DIR / "exports"
                docops.EXPORTS_DIR.mkdir()
                slides = [
                    {
                        "layout": "title",
                        "title": "Validated PowerPoint",
                        "subtitle": "Exact requested format",
                    }
                ]
                drafted = docops.draft_presentation(
                    "Validated PowerPoint",
                    "Internal stakeholders",
                    json.dumps(slides),
                    finalize=True,
                )
                exported = docops.export_document(drafted["doc_id"], "pptx")
                function_response = obj(
                    id="resp_export",
                    output=[
                        obj(
                            type="function_call",
                            name="export_document",
                            call_id="call_export",
                            arguments=json.dumps(
                                {
                                    "doc_id": drafted["doc_id"],
                                    "format": "pptx",
                                }
                            ),
                        )
                    ],
                )
                client = FakeClient(
                    [
                        [obj(type="response.completed", response=function_response)],
                        final_stream("Presentation completed."),
                    ]
                )
                events = list(
                    responses_runtime.ResponsesOrchestrator(
                        client,
                        self.cfg,
                        dispatcher=lambda _name, _arguments: exported,
                    ).stream_turn("Create a PowerPoint presentation")
                )
            finally:
                docops._DB_PATH = old_db
                docops.DOCS_DIR = old_docs
                docops.EXPORTS_DIR = old_exports

        artifact_events = [event for event in events if event["type"] == "artifact.ready"]
        self.assertEqual(len(artifact_events), 1)
        self.assertEqual(artifact_events[0]["format"], "pptx")
        self.assertNotIn("path", artifact_events[0])
        provider_result = json.loads(client.responses.calls[1]["input"][0]["output"])
        self.assertNotIn("path", provider_result)
        self.assertEqual(
            events[-1]["deliverable"],
            {"status": "ready", "requested_format": "pptx"},
        )

    def test_mcp_approval_request_pauses_without_provider_id_exposure(self):
        response = obj(
            id="resp_mcp_pending",
            output_text="",
            output=[
                obj(
                    type="mcp_approval_request",
                    id="mcp_provider_approval",
                    server_label="github",
                    name="create_issue",
                    arguments='{"title":"Test"}',
                )
            ],
        )
        client = FakeClient([[obj(type="response.completed", response=response)]])
        events = list(
            responses_runtime.ResponsesOrchestrator(client, self.cfg).stream_turn("Create an issue")
        )
        approval = events[-1]
        self.assertEqual(approval["type"], "approval.required")
        self.assertEqual(approval["approval_kind"], "mcp")
        self.assertEqual(approval["arguments"], {"title": "Test"})
        self.assertEqual(approval["_response_id"], "resp_mcp_pending")
        self.assertEqual(approval["_provider_item_id"], "mcp_provider_approval")

    def test_local_policy_approval_pauses_before_dispatch(self):
        response = obj(
            id="resp_local_pending",
            output_text="",
            output=[
                obj(
                    type="function_call",
                    call_id="call_approved",
                    name="approve_codeops_task",
                    arguments='{"task_id":"task","approval_evidence":"owner"}',
                )
            ],
        )
        client = FakeClient([[obj(type="response.completed", response=response)]])
        dispatched = []
        events = list(
            responses_runtime.ResponsesOrchestrator(
                client,
                self.cfg,
                dispatcher=lambda name, arguments: dispatched.append((name, arguments)),
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
        client = FakeClient(
            [
                final_stream(
                    "A detailed delegated answer.",
                    annotations=[citation],
                )
            ]
        )
        result = responses_runtime.delegate_advanced_task(
            "Research this", client=client, cfg=self.cfg
        )
        self.assertEqual(result["summary"], "A detailed delegated answer.")
        self.assertEqual(result["details"]["citations"][0]["title"], "Evidence")

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
        self.temp_path = Path(self.temp_dir.name)
        self.old_db_path = chatlog._DB_PATH
        self.old_docops_db_path = docops._DB_PATH
        self.old_docs_dir = docops.DOCS_DIR
        self.old_exports_dir = docops.EXPORTS_DIR
        chatlog._DB_PATH = self.temp_path / "test.sqlite3"
        docops._DB_PATH = chatlog._DB_PATH
        docops.DOCS_DIR = self.temp_path / "documents"
        docops.DOCS_DIR.mkdir()
        docops.EXPORTS_DIR = docops.DOCS_DIR / "exports"
        docops.EXPORTS_DIR.mkdir()
        self.env = patch.dict(
            os.environ,
            {
                "PJ_TOOL_BRIDGE_TOKEN": "bridge-secret",
                "OPENAI_API_KEY": "test-openai-key",
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
        self.auth = {"Authorization": "Bearer bridge-secret"}

    def tearDown(self):
        self.prompt_perfecting.stop()
        self.env.stop()
        chatlog._DB_PATH = self.old_db_path
        docops._DB_PATH = self.old_docops_db_path
        docops.DOCS_DIR = self.old_docs_dir
        docops.EXPORTS_DIR = self.old_exports_dir
        self.temp_dir.cleanup()

    def _create_powerpoint_artifact(self):
        slides = [
            {
                "layout": "title",
                "title": "Web PowerPoint",
                "subtitle": "Authenticated artifact delivery",
            }
        ]
        drafted = docops.draft_presentation(
            "Web PowerPoint",
            "Internal stakeholders",
            json.dumps(slides),
            finalize=True,
        )
        return docops.export_document(drafted["doc_id"], "pptx")

    def test_routes_require_bridge_auth(self):
        response = self.client.get("/responses/capabilities")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["code"], "bridge_auth_required")

    def test_prompt_perfecting_route_is_authenticated_and_typed(self):
        response = self.client.post(
            "/responses/prompt-perfect",
            json={"prompt": "Draft a memo", "surface": "full_power"},
            headers=self.auth,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["prompt"]
        self.assertEqual(payload["refined_prompt"], "Draft a memo")
        self.assertEqual(payload["surface"], "full_power")
        self.assertNotIn("original_prompt", payload)

    def test_tool_schema_bridge_seals_authoritative_instructions(self):
        response = self.client.get("/tool-schemas", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["contract_version"], realtime_server.CONTRACT_VERSION)
        self.assertEqual(payload["count"], len(realtime_server._function_tool_schemas()))
        self.assertEqual(
            payload["instructions_sha256"],
            hashlib.sha256(payload["instructions"].encode()).hexdigest(),
        )
        expected_tools = json.dumps(
            payload["tools"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        self.assertEqual(
            payload["tool_manifest_sha256"],
            hashlib.sha256(expected_tools).hexdigest(),
        )
        self.assertTrue(payload["instruction_files"])
        self.assertEqual(
            payload["prompt_perfecting_version"],
            realtime_server.promptops.PROMPT_PERFECTING_VERSION,
        )
        self.assertRegex(payload["tool_policy_sha256"], r"^[a-f0-9]{64}$")

    def test_realtime_tool_artifact_is_linked_to_durable_session(self):
        session = chatlog.new_session(channel="realtime")
        exported = self._create_powerpoint_artifact()
        with patch.object(
            responses_runtime,
            "dispatch_local_function",
            return_value=exported,
        ):
            direct = responses_runtime.dispatch_realtime_function(
                "export_document",
                {},
            )
            response = self.client.post(
                "/execute-tool",
                json={
                    "name": "export_document",
                    "arguments": {},
                    "session_id": session["id"],
                },
                headers=self.auth,
            )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        expected_download_url = exported["artifact"]["download_url"]
        self.assertEqual(
            direct["artifact"]["download_url"],
            expected_download_url,
        )
        self.assertEqual(
            payload["artifact"]["download_url"],
            expected_download_url,
        )
        self.assertNotIn("path", direct)
        self.assertNotIn("path", payload)
        self.assertNotIn("/Users/", json.dumps(payload))
        self.assertEqual(
            chatlog.list_session_artifact_ids(session["id"]),
            [exported["artifact"]["artifact_id"]],
        )

    def test_realtime_messages_are_idempotent_and_resumable(self):
        created = self.client.post(
            "/responses/sessions",
            json={"title": "", "channel": "realtime"},
            headers=self.auth,
        )
        self.assertEqual(created.status_code, 201)
        session_id = created.get_json()["session"]["id"]
        body = {
            "external_id": "item_audio_1",
            "role": "user",
            "content": "first transcript",
            "source": "input_audio",
            "status": "completed",
            "metadata": {
                "prompt_perfecting_version": "1.0",
                "refined_prompt": "First transcript.",
            },
        }
        first = self.client.post(
            f"/responses/sessions/{session_id}/realtime-messages",
            json=body,
            headers=self.auth,
        )
        second = self.client.post(
            f"/responses/sessions/{session_id}/realtime-messages",
            json=body,
            headers=self.auth,
        )
        resumed = self.client.post(
            f"/responses/sessions/{session_id}/resume",
            json={},
            headers=self.auth,
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        history = resumed.get_json()["session"]["history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["content"], "first transcript")
        self.assertEqual(history[0]["external_id"], "item_audio_1")
        self.assertEqual(history[0]["source"], "input_audio")
        self.assertEqual(
            history[0]["metadata"]["refined_prompt"],
            "First transcript.",
        )

    def test_interrupted_realtime_message_preserves_first_boundary(self):
        session = chatlog.new_session(channel="realtime")
        body = {
            "external_id": "assistant_item_1",
            "role": "assistant",
            "content": "Partial answer",
            "source": "output_text",
            "response_id": "response_1",
            "status": "interrupted",
            "playback_ms": 1250,
            "metadata": {},
        }
        first = self.client.post(
            f"/responses/sessions/{session['id']}/realtime-messages",
            json=body,
            headers=self.auth,
        )
        first_history = chatlog.history(session["id"])
        second = self.client.post(
            f"/responses/sessions/{session['id']}/realtime-messages",
            json=body,
            headers=self.auth,
        )
        second_history = chatlog.history(session["id"])
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(second_history), 1)
        self.assertEqual(second_history[0]["source"], "output_text")
        self.assertEqual(second_history[0]["status"], "interrupted")
        self.assertEqual(
            first_history[0]["interrupted_at"],
            second_history[0]["interrupted_at"],
        )

    def test_realtime_message_metadata_is_strict_and_bounded(self):
        session = chatlog.new_session(channel="realtime")
        response = self.client.post(
            f"/responses/sessions/{session['id']}/realtime-messages",
            json={
                "external_id": "item_invalid_metadata",
                "role": "user",
                "content": "Hello",
                "source": "typed",
                "status": "completed",
                "metadata": {"unexpected": "value"},
            },
            headers=self.auth,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "invalid_realtime_message",
        )

    def test_realtime_external_id_cannot_mutate_terminal_message(self):
        session = chatlog.new_session(channel="realtime")
        original = {
            "external_id": "immutable_item_1",
            "role": "user",
            "content": "Original transcript",
            "source": "input_audio",
            "status": "completed",
            "metadata": {},
        }
        first = self.client.post(
            f"/responses/sessions/{session['id']}/realtime-messages",
            json=original,
            headers=self.auth,
        )
        changed = self.client.post(
            f"/responses/sessions/{session['id']}/realtime-messages",
            json={**original, "content": "Mutated transcript"},
            headers=self.auth,
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(changed.status_code, 400)
        history = chatlog.history(session["id"])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["content"], "Original transcript")

    def test_full_power_stores_original_but_submits_refined_prompt(self):
        created = self.client.post(
            "/responses/sessions",
            json={"title": "Prompt history"},
            headers=self.auth,
        )
        session_id = created.get_json()["session"]["id"]
        original = "make a board memo"
        refined = "Create a concise board memo."
        fake_client = FakeClient([final_stream("Complete.")])
        result = {
            "original_prompt": original,
            "refined_prompt": refined,
            "changed": True,
            "version": "1.0",
            "surface": "full_power",
            "intent_summary": "Create a board memo.",
            "constraints_preserved": [],
            "original_sha256": hashlib.sha256(original.encode()).hexdigest(),
            "refined_sha256": hashlib.sha256(refined.encode()).hexdigest(),
        }

        with (
            patch.object(
                realtime_server.promptops,
                "perfect_prompt",
                return_value=result,
            ),
            patch.object(
                realtime_server,
                "OPENAI_CLIENT_FACTORY",
                return_value=fake_client,
            ),
        ):
            response = self.client.post(
                f"/responses/sessions/{session_id}/turns",
                json={"message": original},
                headers=self.auth,
                buffered=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: prompt.perfected", response.get_data(as_text=True))
        self.assertEqual(
            chatlog.history(session_id)[0]["content"],
            original,
        )
        self.assertEqual(fake_client.responses.calls[0]["input"], refined)

    def test_prompt_failure_preserves_original_turn_and_releases_claim(self):
        session = chatlog.new_session(channel="web")
        original = "Preserve this exact request"
        with patch.object(
            realtime_server.promptops,
            "perfect_prompt",
            side_effect=realtime_server.promptops.PromptPerfectingError(
                "prompt_perfecting_provider_error",
                "Prompt perfecting is temporarily unavailable.",
            ),
        ):
            response = self.client.post(
                f"/responses/sessions/{session['id']}/turns",
                json={"message": original},
                headers=self.auth,
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "prompt_perfecting_provider_error",
        )
        self.assertEqual(chatlog.history(session["id"])[0]["content"], original)
        token = chatlog.claim_session_turn(session["id"])
        self.assertIsNotNone(token)
        chatlog.release_session_turn(session["id"], token)

    def test_responses_routes_fail_closed_without_bridge_token(self):
        with patch.dict(os.environ, {"PJ_TOOL_BRIDGE_TOKEN": ""}):
            response = self.client.get("/responses/capabilities")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "bridge_auth_not_configured",
        )

    def test_local_web_session_authorizes_same_origin_browser_only(self):
        with (
            patch.dict(os.environ, {"PJ_TOOL_BRIDGE_TOKEN": ""}),
            patch.dict(
                realtime_server.app.config,
                {"LOCAL_WEB_OWNER_SESSION_ENABLED": True},
            ),
        ):
            loaded = self.client.get(
                "/",
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )
            self.assertEqual(loaded.status_code, 200)
            loaded.close()
            allowed = self.client.get(
                "/responses/capabilities",
                headers={"Referer": "http://localhost/"},
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )
            denied = self.client.get(
                "/responses/capabilities",
                headers={"Origin": "https://attacker.example"},
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(denied.status_code, 503)
        self.assertEqual(
            denied.get_json()["error"]["code"],
            "bridge_auth_not_configured",
        )

    def test_builtin_web_client_rejects_non_loopback_requests(self):
        with patch.dict(
            realtime_server.app.config,
            {"LOCAL_WEB_OWNER_SESSION_ENABLED": True},
        ):
            response = self.client.get(
                "/",
                environ_base={"REMOTE_ADDR": "192.0.2.10"},
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "local_web_only",
        )

    def test_builtin_web_client_is_disabled_without_explicit_local_mode(self):
        with patch.dict(
            realtime_server.app.config,
            {"LOCAL_WEB_OWNER_SESSION_ENABLED": False},
        ):
            response = self.client.get(
                "/",
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )
        self.assertEqual(response.status_code, 403)

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
        self.assertEqual([item["role"] for item in detail["history"]], ["user", "assistant"])
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
            output=[
                obj(
                    type="mcp_approval_request",
                    id="mcp_provider_approval",
                    server_label="github",
                    name="create_issue",
                    arguments='{"title":"Owner approved"}',
                )
            ],
        )
        first_client = FakeClient([[obj(type="response.completed", response=pending_response)]])
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
        approval = next(event for event in events if event["type"] == "approval.required")
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

        continuation_client = FakeClient([final_stream("The approved MCP action completed.")])
        with patch.object(
            realtime_server,
            "OPENAI_CLIENT_FACTORY",
            return_value=continuation_client,
        ):
            resolved = self.client.post(
                (f"/responses/sessions/{session['id']}/approvals/{approval['approval_id']}"),
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
        self.assertEqual(
            call["input"],
            [
                {
                    "type": "mcp_approval_response",
                    "approval_request_id": "mcp_provider_approval",
                    "approve": True,
                }
            ],
        )

        detail = chatlog.session_detail(session["id"])
        self.assertEqual(detail["pending_approvals"], [])
        self.assertEqual(
            [item["role"] for item in detail["history"]],
            ["user", "assistant"],
        )

    def test_deliverable_requirement_persists_across_approval_pause(self):
        session = chatlog.new_session(channel="web")
        pending_response = obj(
            id="resp_pptx_approval",
            output_text="",
            output=[
                obj(
                    type="mcp_approval_request",
                    id="mcp_pptx_approval",
                    server_label="github",
                    name="search_repository",
                    arguments='{"query":"PJ evidence"}',
                )
            ],
        )
        with patch.object(
            realtime_server,
            "OPENAI_CLIENT_FACTORY",
            return_value=FakeClient([[obj(type="response.completed", response=pending_response)]]),
        ):
            response = self.client.post(
                f"/responses/sessions/{session['id']}/turns",
                json={"message": "Create a PowerPoint presentation with evidence"},
                headers=self.auth,
                buffered=True,
            )
        approval = next(
            json.loads(line.removeprefix("data: "))
            for line in response.get_data(as_text=True).splitlines()
            if line.startswith("data: ") and '"type": "approval.required"' in line
        )
        pending = chatlog.get_pending_approval(session["id"], approval["approval_id"])
        self.assertEqual(pending["deliverable_format"], "pptx")

    def test_ready_artifact_persists_across_approval_resume(self):
        session = chatlog.new_session(channel="web")
        artifact = self._create_powerpoint_artifact()["artifact"]
        self.assertTrue(chatlog.link_session_artifact(session["id"], artifact["artifact_id"]))
        turn_token = chatlog.claim_session_turn(session["id"])
        pending = chatlog.pause_session_turn_for_approval(
            session,
            turn_token,
            approval_kind="mcp",
            provider_response_id="resp_before_approval",
            provider_item_id="mcp_approval_item",
            tool_name="search_repository",
            server_label="github",
            arguments={"query": "PJ evidence"},
            deliverable_format="pptx",
            artifact_ids=[artifact["artifact_id"]],
            artifact_hashes={artifact["artifact_id"]: artifact["sha256"]},
        )
        self.assertEqual(pending["artifact_ids"], [artifact["artifact_id"]])

        continuation_client = FakeClient([final_stream("The presentation and evidence are ready.")])
        with patch.object(
            realtime_server,
            "OPENAI_CLIENT_FACTORY",
            return_value=continuation_client,
        ):
            resolved = self.client.post(
                (f"/responses/sessions/{session['id']}/approvals/{pending['approval_id']}"),
                json={"approve": True},
                headers=self.auth,
                buffered=True,
            )

        self.assertEqual(resolved.status_code, 200)
        events = [
            json.loads(line.removeprefix("data: "))
            for line in resolved.get_data(as_text=True).splitlines()
            if line.startswith("data: ")
        ]
        completion = next(event for event in events if event["type"] == "completion")
        self.assertEqual(completion["deliverable"]["status"], "ready")
        self.assertEqual(completion["artifacts"][0]["artifact_id"], artifact["artifact_id"])
        self.assertEqual(len(continuation_client.responses.calls), 1)

    def test_approved_local_action_can_complete_deliverable(self):
        session = chatlog.new_session(channel="web")
        exported = self._create_powerpoint_artifact()
        turn_token = chatlog.claim_session_turn(session["id"])
        pending = chatlog.pause_session_turn_for_approval(
            session,
            turn_token,
            approval_kind="local_function",
            provider_response_id="resp_export_approval",
            provider_item_id="export_call",
            tool_name="export_document",
            arguments={"doc_id": exported["doc_id"], "format": "pptx"},
            deliverable_format="pptx",
        )
        continuation_client = FakeClient([final_stream("The approved PowerPoint is ready.")])
        with (
            patch.object(
                realtime_server,
                "OPENAI_CLIENT_FACTORY",
                return_value=continuation_client,
            ),
            patch.object(
                realtime_server.skills,
                "dispatch",
                return_value=exported,
            ),
        ):
            resolved = self.client.post(
                (f"/responses/sessions/{session['id']}/approvals/{pending['approval_id']}"),
                json={"approve": True},
                headers=self.auth,
                buffered=True,
            )

        self.assertEqual(resolved.status_code, 200)
        body = resolved.get_data(as_text=True)
        self.assertIn("event: artifact.ready", body)
        self.assertNotIn(str(docops.EXPORTS_DIR), body)
        events = [
            json.loads(line.removeprefix("data: "))
            for line in body.splitlines()
            if line.startswith("data: ")
        ]
        completion = next(event for event in events if event["type"] == "completion")
        self.assertEqual(completion["deliverable"]["status"], "ready")
        self.assertEqual(len(continuation_client.responses.calls), 1)

    def test_failed_approval_artifact_validation_is_retryable(self):
        session = chatlog.new_session(channel="web")
        artifact = self._create_powerpoint_artifact()["artifact"]
        turn_token = chatlog.claim_session_turn(session["id"])
        pending = chatlog.pause_session_turn_for_approval(
            session,
            turn_token,
            approval_kind="mcp",
            provider_response_id="resp_bad_artifact",
            provider_item_id="mcp_bad_artifact",
            tool_name="search_repository",
            server_label="github",
            arguments={"query": "evidence"},
            deliverable_format="pptx",
            artifact_ids=[artifact["artifact_id"]],
            artifact_hashes={artifact["artifact_id"]: "0" * 64},
        )

        failed = self.client.post(
            (f"/responses/sessions/{session['id']}/approvals/{pending['approval_id']}"),
            json={"approve": True},
            headers=self.auth,
        )
        self.assertEqual(failed.status_code, 409)
        self.assertEqual(
            failed.get_json()["error"]["code"],
            "approval_artifact_validation_failed",
        )
        retryable = chatlog.get_pending_approval(session["id"], pending["approval_id"])
        self.assertIsNotNone(retryable)
        self.assertEqual(retryable["status"], "pending")

    def test_failed_approval_continuation_reuses_persisted_tool_result(self):
        session = chatlog.new_session(channel="web")
        turn_token = chatlog.claim_session_turn(session["id"])
        pending = chatlog.pause_session_turn_for_approval(
            session,
            turn_token,
            approval_kind="local_function",
            provider_response_id="resp_retryable_approval",
            provider_item_id="retryable_call",
            tool_name="approve_codeops_task",
            arguments={"task_id": "task-123"},
        )
        failed_client = FakeClient(
            [
                [
                    obj(
                        type="response.failed",
                        error=obj(message="temporary provider failure"),
                    )
                ]
            ]
        )
        successful_client = FakeClient([final_stream("The approved action is complete.")])

        with patch.object(
            realtime_server.skills,
            "dispatch",
            return_value={"approval_state": "approved"},
        ) as dispatch:
            with patch.object(
                realtime_server,
                "OPENAI_CLIENT_FACTORY",
                return_value=failed_client,
            ):
                failed = self.client.post(
                    (f"/responses/sessions/{session['id']}/approvals/{pending['approval_id']}"),
                    json={"approve": True},
                    headers=self.auth,
                    buffered=True,
                )

            self.assertEqual(failed.status_code, 200)
            self.assertIn("responses_turn_failed", failed.get_data(as_text=True))
            retryable = chatlog.get_pending_approval(session["id"], pending["approval_id"])
            self.assertEqual(retryable["status"], "executing_approved")
            self.assertTrue(retryable["execution_result_recorded"])

            changed_decision = self.client.post(
                (f"/responses/sessions/{session['id']}/approvals/{pending['approval_id']}"),
                json={"approve": False},
                headers=self.auth,
            )
            self.assertEqual(changed_decision.status_code, 409)

            with patch.object(
                realtime_server,
                "OPENAI_CLIENT_FACTORY",
                return_value=successful_client,
            ):
                completed = self.client.post(
                    (f"/responses/sessions/{session['id']}/approvals/{pending['approval_id']}"),
                    json={"approve": True},
                    headers=self.auth,
                    buffered=True,
                )

        self.assertEqual(completed.status_code, 200)
        self.assertIn("event: approval.resolved", completed.get_data(as_text=True))
        self.assertIn("event: completion", completed.get_data(as_text=True))
        self.assertEqual(dispatch.call_count, 1)
        self.assertIsNone(chatlog.get_pending_approval(session["id"], pending["approval_id"]))

    def test_reserved_approved_effect_is_never_replayed_after_crash_gap(self):
        session = chatlog.new_session(channel="web")
        turn_token = chatlog.claim_session_turn(session["id"])
        pending = chatlog.pause_session_turn_for_approval(
            session,
            turn_token,
            approval_kind="local_function",
            provider_response_id="resp_crash_gap",
            provider_item_id="call_crash_gap",
            tool_name="approve_codeops_task",
            arguments={"task_id": "task-crash-gap"},
        )
        chatlog.begin_pending_approval_execution(session["id"], pending["approval_id"], True)
        reservation = chatlog.reserve_tool_execution(
            session["id"],
            f"approval:{pending['approval_id']}",
            "approve_codeops_task",
            {"task_id": "task-crash-gap"},
            approval_id=pending["approval_id"],
        )
        self.assertEqual(reservation["state"], "reserved")

        with patch.object(realtime_server.skills, "dispatch") as dispatch:
            response = self.client.post(
                (f"/responses/sessions/{session['id']}/approvals/{pending['approval_id']}"),
                json={"approve": True},
                headers=self.auth,
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "approval_execution_outcome_unknown",
        )
        dispatch.assert_not_called()
        self.assertIsNone(chatlog.get_pending_approval(session["id"], pending["approval_id"]))
        with chatlog._db() as conn:
            execution_status = conn.execute(
                "SELECT status FROM chat_tool_executions WHERE session_id=? AND execution_key=?",
                (
                    session["id"],
                    f"approval:{pending['approval_id']}",
                ),
            ).fetchone()[0]
            approval_status = conn.execute(
                "SELECT status FROM chat_pending_approvals WHERE id=?",
                (pending["approval_id"],),
            ).fetchone()[0]
        self.assertEqual(execution_status, "outcome_unknown")
        self.assertEqual(approval_status, "execution_unknown")

    def test_approval_retry_reuses_follow_on_result_and_idempotency_keys(self):
        session = chatlog.new_session(channel="web")
        turn_token = chatlog.claim_session_turn(session["id"])
        pending = chatlog.pause_session_turn_for_approval(
            session,
            turn_token,
            approval_kind="local_function",
            provider_response_id="resp_before_follow_on",
            provider_item_id="approved_call",
            tool_name="approve_codeops_task",
            arguments={"task_id": "task-follow-on"},
        )
        follow_on_response = obj(
            id="resp_follow_on_round",
            output=[
                obj(
                    type="function_call",
                    name="get_current_time",
                    call_id="follow_on_call",
                    arguments="{}",
                )
            ],
        )
        failed_client = FakeClient(
            [
                [
                    obj(
                        type="response.created",
                        response=obj(id="resp_follow_on_round"),
                    ),
                    obj(type="response.completed", response=follow_on_response),
                ],
                [
                    obj(
                        type="response.created",
                        response=obj(id="resp_follow_on_final"),
                    ),
                    obj(
                        type="response.failed",
                        error=obj(message="temporary provider failure"),
                    ),
                ],
            ]
        )
        successful_client = FakeClient(
            [
                [
                    obj(
                        type="response.created",
                        response=obj(id="resp_follow_on_round"),
                    ),
                    obj(type="response.completed", response=follow_on_response),
                ],
                final_stream(
                    "The approved action is complete.",
                    response_id="resp_follow_on_final",
                ),
            ]
        )

        def dispatch(name, arguments, *, approval_granted=False):
            if name == "approve_codeops_task":
                self.assertTrue(approval_granted)
                return {"approval_state": "approved"}
            self.assertEqual((name, arguments), ("get_current_time", {}))
            self.assertFalse(approval_granted)
            return {"iso8601": "2026-01-01T00:00:00Z"}

        with patch.object(
            realtime_server.skills, "dispatch", side_effect=dispatch
        ) as dispatch_mock:
            with patch.object(
                realtime_server,
                "OPENAI_CLIENT_FACTORY",
                return_value=failed_client,
            ):
                first = self.client.post(
                    (f"/responses/sessions/{session['id']}/approvals/{pending['approval_id']}"),
                    json={"approve": True},
                    headers=self.auth,
                    buffered=True,
                )
            self.assertIn("responses_turn_failed", first.get_data(as_text=True))

            with patch.object(
                realtime_server,
                "OPENAI_CLIENT_FACTORY",
                return_value=successful_client,
            ):
                second = self.client.post(
                    (f"/responses/sessions/{session['id']}/approvals/{pending['approval_id']}"),
                    json={"approve": True},
                    headers=self.auth,
                    buffered=True,
                )

        self.assertIn("event: completion", second.get_data(as_text=True))
        self.assertEqual(dispatch_mock.call_count, 2)
        self.assertEqual(
            [call["extra_headers"]["Idempotency-Key"] for call in failed_client.responses.calls],
            [
                call["extra_headers"]["Idempotency-Key"]
                for call in successful_client.responses.calls
            ],
        )

    def test_provider_idempotency_conflict_fails_closed(self):
        session = chatlog.new_session(channel="web")
        turn_token = chatlog.claim_session_turn(session["id"])
        pending = chatlog.pause_session_turn_for_approval(
            session,
            turn_token,
            approval_kind="mcp",
            provider_response_id="resp_mcp_before_conflict",
            provider_item_id="mcp_conflict_call",
            tool_name="search_repository",
            server_label="github",
            arguments={"query": "evidence"},
        )
        first_client = FakeClient(
            [
                [
                    obj(type="response.created", response=obj(id="resp_stable")),
                    obj(
                        type="response.failed",
                        error=obj(message="temporary provider failure"),
                    ),
                ]
            ]
        )
        conflicting_client = FakeClient(
            [
                [
                    obj(type="response.created", response=obj(id="resp_conflict")),
                ]
            ]
        )
        with patch.object(
            realtime_server,
            "OPENAI_CLIENT_FACTORY",
            return_value=first_client,
        ):
            first = self.client.post(
                (f"/responses/sessions/{session['id']}/approvals/{pending['approval_id']}"),
                json={"approve": True},
                headers=self.auth,
                buffered=True,
            )
        self.assertIn("responses_turn_failed", first.get_data(as_text=True))

        with patch.object(
            realtime_server,
            "OPENAI_CLIENT_FACTORY",
            return_value=conflicting_client,
        ):
            second = self.client.post(
                (f"/responses/sessions/{session['id']}/approvals/{pending['approval_id']}"),
                json={"approve": True},
                headers=self.auth,
                buffered=True,
            )
        body = second.get_data(as_text=True)
        self.assertIn("approval_execution_outcome_unknown", body)
        self.assertEqual(
            first_client.responses.calls[0]["extra_headers"]["Idempotency-Key"],
            conflicting_client.responses.calls[0]["extra_headers"]["Idempotency-Key"],
        )
        self.assertIsNone(chatlog.get_pending_approval(session["id"], pending["approval_id"]))

    def test_local_approval_executes_only_after_trusted_resolution(self):
        session = chatlog.new_session(channel="web")
        pending_response = obj(
            id="resp_local_pending",
            output_text="",
            output=[
                obj(
                    type="function_call",
                    call_id="call_local_approval",
                    name="approve_codeops_task",
                    arguments=('{"task_id":"task-123","approval_evidence":"owner click"}'),
                )
            ],
        )
        with patch.object(
            realtime_server,
            "OPENAI_CLIENT_FACTORY",
            return_value=FakeClient([[obj(type="response.completed", response=pending_response)]]),
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
            if line.startswith("data: ") and '"type": "approval.required"' in line
        )

        continuation_client = FakeClient(
            [final_stream("The owner-approved local action completed.")]
        )
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
                (f"/responses/sessions/{session['id']}/approvals/{approval['approval_id']}"),
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
        self.assertEqual(response.get_json()["error"]["code"], "invalid_request_body")

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
        self.assertEqual(fake_client.responses.calls[0]["text"]["format"]["name"], "answer")

    def test_capability_contract_reports_direct_function_count(self):
        response = self.client.get("/responses/capabilities", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        capabilities = response.get_json()["capabilities"]
        self.assertEqual(
            capabilities["local_functions"]["count"],
            len(skills.TOOL_SCHEMAS),
        )
        self.assertNotIn("headers", json.dumps(capabilities))

    def test_artifact_download_is_authenticated_integrity_checked_and_persistent(self):
        session = chatlog.new_session(channel="web")
        exported = self._create_powerpoint_artifact()
        artifact = exported["artifact"]
        self.assertTrue(chatlog.link_session_artifact(session["id"], artifact["artifact_id"]))

        unauthenticated = self.client.get(artifact["download_url"])
        self.assertEqual(unauthenticated.status_code, 401)
        downloaded = self.client.get(artifact["download_url"], headers=self.auth)
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.mimetype, artifact["mime_type"])
        self.assertIn("attachment;", downloaded.headers["Content-Disposition"])
        self.assertEqual(downloaded.headers["ETag"], f'"sha256-{artifact["sha256"]}"')
        self.assertEqual(downloaded.headers["Cache-Control"], "private, no-store")
        downloaded.close()

        listed = self.client.get(
            f"/responses/sessions/{session['id']}/artifacts",
            headers=self.auth,
        )
        self.assertEqual(listed.get_json()["artifacts"][0]["artifact_id"], artifact["artifact_id"])
        resumed = self.client.post(
            f"/responses/sessions/{session['id']}/resume",
            json={},
            headers=self.auth,
        )
        self.assertEqual(
            resumed.get_json()["session"]["artifacts"][0]["sha256"],
            artifact["sha256"],
        )
        self.assertNotIn(str(self.temp_path), json.dumps(resumed.get_json()))

        snapshot_artifact, snapshot = docops.open_export_artifact_snapshot(artifact["artifact_id"])
        registered = docops.resolve_export_artifact(artifact["artifact_id"], include_path=True)
        original_bytes = snapshot.read()
        Path(registered["path"]).write_bytes(b"replacement")
        snapshot.seek(0)
        self.assertEqual(snapshot_artifact["sha256"], artifact["sha256"])
        self.assertEqual(snapshot.read(), original_bytes)
        snapshot.close()

        blocked = self.client.get(artifact["download_url"], headers=self.auth)
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.get_json()["error"]["code"], "artifact_unavailable")

    def test_every_document_format_is_downloadable_from_chat(self):
        session = chatlog.new_session(channel="web")
        drafted = docops.draft_document(
            "status_report",
            "Downloadable Formats",
            json.dumps(
                {
                    "Period": "Q3",
                    "Highlights": "Universal downloads",
                    "Metrics": "Five formats",
                    "Blockers": "None",
                    "Next Period Plan": "Release",
                }
            ),
            finalize=True,
        )
        artifacts = [drafted["artifact"]]

        def convert(command, **_kwargs):
            output = Path(command[5])
            output.write_bytes(Path(command[3]).read_bytes() + command[2].encode())
            return docops.subprocess.CompletedProcess(command, 0, "", "")

        artifacts.append(docops.export_document(drafted["doc_id"], "html")["artifact"])
        with patch.object(docops.subprocess, "run", side_effect=convert):
            artifacts.extend(
                docops.export_document(drafted["doc_id"], format_name)["artifact"]
                for format_name in ("docx", "rtf")
            )
        artifacts.append(self._create_powerpoint_artifact()["artifact"])

        expected_mime = {
            "md": "text/markdown",
            "html": "text/html",
            "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            "rtf": "application/rtf",
            "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        }
        self.assertEqual({item["format"] for item in artifacts}, set(expected_mime))
        for artifact in artifacts:
            self.assertTrue(chatlog.link_session_artifact(session["id"], artifact["artifact_id"]))
            downloaded = self.client.get(artifact["download_url"], headers=self.auth)
            self.assertEqual(downloaded.status_code, 200)
            self.assertEqual(downloaded.mimetype, expected_mime[artifact["format"]])
            self.assertEqual(
                hashlib.sha256(downloaded.data).hexdigest(),
                artifact["sha256"],
            )
            downloaded.close()

        listed = self.client.get(
            f"/responses/sessions/{session['id']}/artifacts",
            headers=self.auth,
        )
        self.assertEqual(len(listed.get_json()["artifacts"]), len(artifacts))

    def test_streamed_artifact_event_is_linked_to_session(self):
        session = chatlog.new_session(channel="web")
        exported = self._create_powerpoint_artifact()
        function_response = obj(
            id="resp_export",
            output=[
                obj(
                    type="function_call",
                    name="export_document",
                    call_id="call_export",
                    arguments=json.dumps(
                        {
                            "doc_id": exported["doc_id"],
                            "format": "pptx",
                        }
                    ),
                )
            ],
        )
        fake_client = FakeClient(
            [
                [obj(type="response.completed", response=function_response)],
                final_stream("The native PowerPoint is ready."),
            ]
        )
        with (
            patch.object(
                realtime_server,
                "OPENAI_CLIENT_FACTORY",
                return_value=fake_client,
            ),
            patch.object(
                realtime_server.skills,
                "dispatch",
                return_value=exported,
            ),
        ):
            response = self.client.post(
                f"/responses/sessions/{session['id']}/turns",
                json={"message": "Create a PowerPoint presentation"},
                headers=self.auth,
                buffered=True,
            )
        body = response.get_data(as_text=True)
        self.assertIn("event: artifact.ready", body)
        self.assertNotIn(str(self.temp_path), body)
        self.assertLess(
            body.index("event: artifact.ready"),
            body.index("event: tool.result"),
        )
        self.assertEqual(
            chatlog.list_session_artifact_ids(session["id"]),
            [exported["artifact"]["artifact_id"]],
        )


class TestRequestedDeliverableFormat(unittest.TestCase):
    """requested_deliverable_format must require creation intent, not just
    a bare mention of a file format, before forcing artifact creation."""

    def test_bare_informational_mentions_do_not_force_artifacts(self):
        informational_messages = (
            "What is a .docx file used for?",
            "Explain HTML",
            "What's the difference between PDF and DOCX?",
            "I opened the pptx you sent yesterday.",
            "Tell me about markdown files.",
            "Is rtf still commonly used?",
            "Why would someone need an xlsx instead of a csv?",
            "What does PDF stand for?",
        )
        for message in informational_messages:
            with self.subTest(message=message):
                self.assertIsNone(responses_runtime.requested_deliverable_format(message))

    def test_genuine_creation_requests_still_enforce_artifacts(self):
        creation_messages = {
            "Create a PDF report for the board.": "pdf",
            "Please export this as a docx file.": "docx",
            "Generate a PowerPoint presentation on Q3 results.": "pptx",
            "Can you build an Excel workbook with the numbers?": "xlsx",
            "Save this as an rtf file.": "rtf",
            "Download the summary as an html file.": "html",
            "Draft a markdown document with the notes.": "md",
            "Produce a Word document summarizing the call.": "docx",
        }
        for message, expected_format in creation_messages.items():
            with self.subTest(message=message):
                self.assertEqual(
                    responses_runtime.requested_deliverable_format(message),
                    expected_format,
                )

    def test_non_string_input_returns_none(self):
        self.assertIsNone(responses_runtime.requested_deliverable_format(None))
        self.assertIsNone(responses_runtime.requested_deliverable_format(42))


if __name__ == "__main__":
    unittest.main()
