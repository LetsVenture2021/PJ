#!/usr/bin/env python3
"""
realtime_server.py — signaling/webhook server that connects PJ to voice.

Provides:
  POST /session    - browser WebRTC signaling (SDP offer in, SDP answer out)
  POST /execute-tool - executes local PJ tools for browser function calls
  POST /webhook    - SIP webhook for inbound phone calls
"""
import hmac
import json
import os
import re
from pathlib import Path
from uuid import uuid4

import requests
from flask import (
    Flask,
    request,
    Response,
    send_file,
    send_from_directory,
    stream_with_context,
)
from flask_cors import CORS
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from openai import OpenAI

import chatlog
import docops
import skills
from realtime_config import realtime_session_config, realtime_tool_schemas
from responses_runtime import (
    capability_manifest,
    dispatch_realtime_function,
    load_config,
    redact_server_paths,
    ResponsesOrchestrator,
)

BASE_DIR = Path(__file__).resolve().parent
CONTRACT_VERSION = "2026-07-28.5"
MAX_ERROR_DETAIL_LENGTH = 320
MAX_SESSION_TITLE_LENGTH = 120
MAX_MESSAGE_LENGTH = 20000
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
ARTIFACT_ID_PATTERN = re.compile(r"^ART-[a-f0-9]{32}$")
SCHEMA_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
OPENAI_CLIENT_FACTORY = OpenAI

app = Flask(__name__, static_folder=str(BASE_DIR / "assets"), static_url_path="/assets")
CORS(app)  # allow local browser origins when running on localhost


def _request_id():
    return request.headers.get("x-pj-client-request-id") or str(uuid4())


def _trim_detail(detail):
    if detail is None:
        return None
    compact = " ".join(
        str(redact_server_paths(str(detail))).split()
    ).strip()
    if not compact:
        return None
    if len(compact) <= MAX_ERROR_DETAIL_LENGTH:
        return compact
    return compact[:MAX_ERROR_DETAIL_LENGTH] + "..."


def _json_response(payload, status=200, req_id=None):
    req_id = req_id or _request_id()
    body = json.dumps(payload)
    resp = Response(body, status=status, mimetype="application/json")
    resp.headers["x-request-id"] = req_id
    resp.headers["x-pj-contract-version"] = CONTRACT_VERSION
    return resp


def _error_response(code, message, status, req_id, detail=None):
    return _json_response(
        {
            "ok": False,
            "error": {
                "code": code,
                "message": message,
                "request_id": req_id,
                "detail": _trim_detail(detail),
            },
        },
        status=status,
        req_id=req_id,
    )


def _check_bridge_auth(req_id, *, required=False):
    expected = (os.getenv("PJ_TOOL_BRIDGE_TOKEN") or "").strip()
    if not expected:
        return _error_response(
            "bridge_auth_not_configured",
            "Bridge authorization is not configured.",
            503,
            req_id,
        )
    provided = request.headers.get("Authorization") or ""
    if hmac.compare_digest(
        provided.encode("utf-8"),
        f"Bearer {expected}".encode("utf-8"),
    ):
        return None
    return _error_response(
        "bridge_auth_required",
        "Bridge authorization failed.",
        401,
        req_id,
    )


def _function_tool_schemas():
    tools = []
    for schema in realtime_tool_schemas():
        if not isinstance(schema, dict):
            continue
        if schema.get("type") != "function":
            continue
        if not isinstance(schema.get("name"), str) or not schema["name"].strip():
            continue
        tools.append(schema)
    return tools


def _validated_json(req_id, *, allowed, required=()):
    if not request.is_json:
        return None, _error_response(
            "invalid_content_type",
            "Expected Content-Type: application/json.",
            415,
            req_id,
        )
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, _error_response(
            "invalid_json",
            "Expected a JSON object.",
            400,
            req_id,
        )
    extras = sorted(set(payload) - set(allowed))
    missing = sorted(set(required) - set(payload))
    if extras or missing:
        detail = {
            "unexpected_fields": extras,
            "missing_fields": missing,
        }
        return None, _error_response(
            "invalid_request_body",
            "Request body does not match the endpoint contract.",
            400,
            req_id,
            detail=json.dumps(detail),
        )
    return payload, None


def _validated_limit(req_id, default=20, maximum=100):
    extras = sorted(set(request.args) - {"limit"})
    if extras:
        return None, _error_response(
            "invalid_query",
            "Unexpected query parameters.",
            400,
            req_id,
            detail=", ".join(extras),
        )
    raw = request.args.get("limit", str(default))
    try:
        limit = int(raw)
    except ValueError:
        limit = 0
    if limit < 1 or limit > maximum:
        return None, _error_response(
            "invalid_limit",
            f"limit must be between 1 and {maximum}.",
            400,
            req_id,
        )
    return limit, None


def _validated_session(session_id, req_id):
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        return None, _error_response(
            "session_not_found",
            "Session was not found.",
            404,
            req_id,
        )
    session = chatlog.get_session(session_id)
    if not session:
        return None, _error_response(
            "session_not_found",
            "Session was not found.",
            404,
            req_id,
        )
    return session, None


def _validated_structured_output(value, req_id):
    if value is None:
        return None, None
    if not isinstance(value, dict) or set(value) - {"name", "schema", "strict"}:
        return None, _error_response(
            "invalid_structured_output",
            "structured_output must contain only name, schema, and strict.",
            400,
            req_id,
        )
    name = value.get("name")
    schema = value.get("schema")
    strict = value.get("strict", True)
    if not isinstance(name, str) or not SCHEMA_NAME_PATTERN.fullmatch(name):
        return None, _error_response(
            "invalid_structured_output",
            "structured_output.name is invalid.",
            400,
            req_id,
        )
    if not isinstance(schema, dict) or not isinstance(strict, bool):
        return None, _error_response(
            "invalid_structured_output",
            "structured_output.schema must be an object and strict must be boolean.",
            400,
            req_id,
        )
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        return None, _error_response(
            "invalid_structured_output_schema",
            "structured_output.schema is not a valid JSON Schema.",
            400,
            req_id,
            detail=exc.message,
        )
    return {
        "type": "json_schema",
        "name": name,
        "schema": schema,
        "strict": strict,
    }, None


def _sse(event):
    event_type = event.get("type", "message")
    return f"event: {event_type}\ndata: {json.dumps(event, default=str)}\n\n"


@app.route("/", methods=["GET"])
def web_client():
    """Serve the PJ web client."""
    return send_from_directory(BASE_DIR, "webrtc_client.html")


@app.route("/health", methods=["GET"])
def health():
    req_id = _request_id()
    return _json_response(
        {
            "ok": True,
            "service": "pj-realtime-server",
            "contract_version": CONTRACT_VERSION,
            "tool_count": len(_function_tool_schemas()),
            "bridge_auth_enabled": bool((os.getenv("PJ_TOOL_BRIDGE_TOKEN") or "").strip()),
            "endpoints": [
                "/session",
                "/token",
                "/execute-tool",
                "/tool-schemas",
                "/responses/capabilities",
                "/responses/sessions",
                "/responses/sessions/search",
                "/responses/sessions/<id>/resume",
                "/responses/sessions/<id>/turns",
                "/responses/sessions/<id>/approvals/<id>",
                "/responses/artifacts/<artifact-id>",
                "/webhook",
                "/health",
            ],
        },
        status=200,
        req_id=req_id,
    )


@app.route("/tool-schemas", methods=["GET"])
def tool_schemas():
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id)
    if auth_error:
        return auth_error
    tools = _function_tool_schemas()
    return _json_response(
        {
            "ok": True,
            "count": len(tools),
            "tools": tools,
        },
        status=200,
        req_id=req_id,
    )


@app.route("/responses/capabilities", methods=["GET"])
def responses_capabilities():
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id, required=True)
    if auth_error:
        return auth_error
    return _json_response(
        {"ok": True, "capabilities": capability_manifest(load_config())},
        req_id=req_id,
    )


@app.route("/responses/sessions", methods=["POST"])
def create_responses_session():
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id, required=True)
    if auth_error:
        return auth_error
    payload, error = _validated_json(req_id, allowed={"title"})
    if error:
        return error
    title = payload.get("title", "")
    if not isinstance(title, str) or len(title.strip()) > MAX_SESSION_TITLE_LENGTH:
        return _error_response(
            "invalid_session_title",
            f"title must be a string up to {MAX_SESSION_TITLE_LENGTH} characters.",
            400,
            req_id,
        )
    session = chatlog.new_session(title.strip(), channel="web")
    session.pop("last_response_id", None)
    return _json_response({"ok": True, "session": session}, 201, req_id)


@app.route("/responses/sessions", methods=["GET"])
def list_responses_sessions():
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id, required=True)
    if auth_error:
        return auth_error
    limit, error = _validated_limit(req_id)
    if error:
        return error
    sessions = chatlog.list_sessions(limit)
    return _json_response(
        {"ok": True, "count": len(sessions), "sessions": sessions},
        req_id=req_id,
    )


@app.route("/responses/sessions/search", methods=["GET"])
def search_responses_sessions():
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id, required=True)
    if auth_error:
        return auth_error
    extras = sorted(set(request.args) - {"q", "limit"})
    query = request.args.get("q", "")
    try:
        limit = int(request.args.get("limit", "20"))
    except ValueError:
        limit = 0
    if extras or not query.strip() or len(query) > 200 or not 1 <= limit <= 100:
        return _error_response(
            "invalid_query",
            "q is required (maximum 200 characters) and limit must be 1-100.",
            400,
            req_id,
            detail=", ".join(extras) if extras else None,
        )
    matches = chatlog.search(query.strip(), limit)
    return _json_response(
        {"ok": True, "count": len(matches), "matches": matches},
        req_id=req_id,
    )


@app.route("/responses/sessions/<session_id>/resume", methods=["POST"])
def resume_responses_session(session_id):
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id, required=True)
    if auth_error:
        return auth_error
    payload, error = _validated_json(req_id, allowed=set())
    if error:
        return error
    session, error = _validated_session(session_id, req_id)
    if error:
        return error
    detail = chatlog.session_detail(session["id"])
    artifacts = []
    for artifact_id in detail.pop("artifact_ids", []):
        artifact = docops.resolve_export_artifact(artifact_id)
        if artifact.get("status") == "ready":
            artifacts.append(artifact)
    detail["artifacts"] = artifacts
    return _json_response({"ok": True, "session": detail}, req_id=req_id)


@app.route("/responses/sessions/<session_id>/turns", methods=["POST"])
def stream_responses_turn(session_id):
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id, required=True)
    if auth_error:
        return auth_error
    session, error = _validated_session(session_id, req_id)
    if error:
        return error
    payload, error = _validated_json(
        req_id,
        allowed={"message", "structured_output"},
        required={"message"},
    )
    if error:
        return error
    message = payload["message"]
    if (
        not isinstance(message, str)
        or not message.strip()
        or len(message) > MAX_MESSAGE_LENGTH
    ):
        return _error_response(
            "invalid_message",
            f"message must be a non-empty string up to {MAX_MESSAGE_LENGTH} characters.",
            400,
            req_id,
        )
    text_format, error = _validated_structured_output(
        payload.get("structured_output"), req_id
    )
    if error:
        return error
    if "OPENAI_API_KEY" not in os.environ:
        return _error_response(
            "missing_openai_api_key",
            "OPENAI_API_KEY is not set.",
            500,
            req_id,
        )

    message = message.strip()
    if chatlog.list_pending_approvals(session["id"]):
        return _error_response(
            "session_approval_pending",
            "Resolve the pending owner approval before starting another turn.",
            409,
            req_id,
        )
    turn_token = chatlog.claim_session_turn(session["id"])
    if not turn_token:
        return _error_response(
            "session_turn_in_progress",
            "Another turn is already in progress for this session.",
            409,
            req_id,
        )
    chatlog.record_turn(session, "user", message)

    response = Response(
        stream_with_context(_stream_session_response(
            session,
            turn_token,
            message,
            previous_response_id=session.get("last_response_id"),
            text_format=text_format,
            req_id=req_id,
        )),
        status=200,
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["x-request-id"] = req_id
    response.headers["x-pj-contract-version"] = CONTRACT_VERSION
    return response


def _stream_session_response(
        session,
        turn_token,
        input_value,
        *,
        previous_response_id,
        text_format,
        req_id,
        prelude=()):
    yield _sse({
        "type": "session",
        "session_id": session["id"],
        "request_id": req_id,
    })
    for event in prelude:
        yield _sse(event)
    try:
        orchestrator = ResponsesOrchestrator(
            OPENAI_CLIENT_FACTORY(), load_config()
        )
        for event in orchestrator.stream_turn(
            input_value,
            previous_response_id=previous_response_id,
            text_format=text_format,
        ):
            public_event = dict(event)
            response_id = public_event.pop("_response_id", None)
            provider_item_id = public_event.pop("_provider_item_id", None)
            if public_event["type"] == "approval.required":
                if not response_id or not provider_item_id:
                    raise RuntimeError(
                        "Approval request did not include provider continuity"
                    )
                pending = chatlog.pause_session_turn_for_approval(
                    session,
                    turn_token,
                    approval_kind=public_event["approval_kind"],
                    provider_response_id=response_id,
                    provider_item_id=provider_item_id,
                    tool_name=public_event.get("name") or "",
                    server_label=public_event.get("server_label") or "",
                    arguments=public_event.get("arguments") or {},
                    text_format=text_format,
                )
                if not pending:
                    raise RuntimeError(
                        "Session turn lease expired before approval was stored"
                    )
                public_event.update({
                    "approval_id": pending["approval_id"],
                    "expires_at": pending["expires_at"],
                    "session_id": session["id"],
                })
                yield _sse(public_event)
                return
            if public_event["type"] == "artifact.ready":
                artifact = docops.resolve_export_artifact(
                    public_event.get("artifact_id", "")
                )
                if artifact.get("status") != "ready":
                    raise RuntimeError("Document artifact failed integrity validation")
                if not chatlog.link_session_artifact(
                    session["id"], artifact["artifact_id"]
                ):
                    raise RuntimeError("Document artifact could not be linked to chat")
                public_event = {"type": "artifact.ready", **artifact}
            if public_event["type"] == "completion":
                stored = chatlog.finish_session_turn(
                    session,
                    turn_token,
                    public_event.get("text", ""),
                    response_id,
                )
                if not stored:
                    raise RuntimeError(
                        "Session turn lease expired before completion"
                    )
                public_event["session_id"] = session["id"]
            yield _sse(public_event)
    except Exception as exc:
        yield _sse({
            "type": "error",
            "error": {
                "code": "responses_turn_failed",
                "message": "Responses turn failed.",
                "request_id": req_id,
                "detail": _trim_detail(exc),
            },
        })
    finally:
        chatlog.release_session_turn(session["id"], turn_token)


@app.route(
    "/responses/sessions/<session_id>/approvals/<approval_id>",
    methods=["POST"],
)
def resolve_responses_approval(session_id, approval_id):
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id, required=True)
    if auth_error:
        return auth_error
    session, error = _validated_session(session_id, req_id)
    if error:
        return error
    if not SESSION_ID_PATTERN.fullmatch(approval_id):
        return _error_response(
            "approval_not_found", "Approval was not found.", 404, req_id
        )
    payload, error = _validated_json(
        req_id, allowed={"approve"}, required={"approve"}
    )
    if error:
        return error
    approve = payload["approve"]
    if not isinstance(approve, bool):
        return _error_response(
            "invalid_approval_decision",
            "approve must be a boolean.",
            400,
            req_id,
        )
    if "OPENAI_API_KEY" not in os.environ:
        return _error_response(
            "missing_openai_api_key",
            "OPENAI_API_KEY is not set.",
            500,
            req_id,
        )
    pending = chatlog.get_pending_approval(session["id"], approval_id)
    if not pending:
        return _error_response(
            "approval_not_found", "Approval was not found.", 404, req_id
        )
    turn_token = chatlog.claim_session_turn(
        session["id"], pending_approval_id=approval_id
    )
    if not turn_token:
        return _error_response(
            "session_turn_in_progress",
            "Another turn is already in progress for this session.",
            409,
            req_id,
        )
    pending = chatlog.decide_pending_approval(
        session["id"], approval_id, approve
    )
    if not pending:
        chatlog.release_session_turn(session["id"], turn_token)
        return _error_response(
            "approval_already_resolved",
            "Approval was already resolved or expired.",
            409,
            req_id,
        )

    prelude = [{
        "type": "approval.resolved",
        "approval_id": approval_id,
        "approval_kind": pending["approval_kind"],
        "name": pending["name"],
        "approved": approve,
    }]
    if pending["approval_kind"] == "mcp":
        continuation = [{
            "type": "mcp_approval_response",
            "approval_request_id": pending["provider_item_id"],
            "approve": approve,
        }]
    else:
        if approve:
            result = skills.dispatch(
                pending["name"],
                pending["arguments"],
                approval_granted=True,
            )
        else:
            result = {"error": "The owner rejected this tool call."}
        artifact = (
            docops.resolve_export_artifact(
                (result.get("artifact") or {}).get("artifact_id", "")
            )
            if isinstance(result, dict)
            else {"status": "not_found"}
        )
        if artifact.get("status") == "ready":
            if not chatlog.link_session_artifact(
                session["id"], artifact["artifact_id"]
            ):
                chatlog.release_session_turn(session["id"], turn_token)
                return _error_response(
                    "artifact_link_failed",
                    "Document artifact could not be linked to chat.",
                    500,
                    req_id,
                )
            prelude.append({"type": "artifact.ready", **artifact})
        prelude.append({
            "type": "tool.result",
            "call_id": pending["provider_item_id"],
            "name": pending["name"],
            "result": redact_server_paths(result),
        })
        continuation = [{
            "type": "function_call_output",
            "call_id": pending["provider_item_id"],
            "output": json.dumps(redact_server_paths(result), default=str),
        }]

    response = Response(
        stream_with_context(_stream_session_response(
            session,
            turn_token,
            continuation,
            previous_response_id=pending["provider_response_id"],
            text_format=pending["text_format"],
            req_id=req_id,
            prelude=prelude,
        )),
        status=200,
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["x-request-id"] = req_id
    response.headers["x-pj-contract-version"] = CONTRACT_VERSION
    return response


@app.route("/responses/artifacts/<artifact_id>", methods=["GET"])
def download_responses_artifact(artifact_id):
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id, required=True)
    if auth_error:
        return auth_error
    if request.args or not ARTIFACT_ID_PATTERN.fullmatch(artifact_id):
        return _error_response(
            "artifact_not_found", "Artifact was not found.", 404, req_id
        )
    artifact, snapshot = docops.open_export_artifact_snapshot(artifact_id)
    if artifact.get("status") == "not_found":
        return _error_response(
            "artifact_not_found", "Artifact was not found.", 404, req_id
        )
    if artifact.get("status") != "ready":
        return _error_response(
            "artifact_unavailable",
            "Artifact failed its integrity check.",
            409,
            req_id,
        )
    response = send_file(
        snapshot,
        mimetype=artifact["mime_type"],
        as_attachment=True,
        download_name=artifact["filename"],
        conditional=False,
        etag=False,
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["ETag"] = f'"sha256-{artifact["sha256"]}"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["x-request-id"] = req_id
    response.headers["x-pj-contract-version"] = CONTRACT_VERSION
    response.call_on_close(snapshot.close)
    return response


@app.route("/session", methods=["POST"])
def webrtc_session():
    """Browser WebRTC signaling endpoint: exchanges SDP for an SDP answer."""
    req_id = _request_id()
    if "OPENAI_API_KEY" not in os.environ:
        return _error_response(
            "missing_openai_api_key",
            "OPENAI_API_KEY is not set.",
            500,
            req_id,
        )

    content_type = request.content_type or ""
    if content_type and "application/sdp" not in content_type:
        return _error_response(
            "invalid_content_type",
            "Expected Content-Type: application/sdp for /session.",
            415,
            req_id,
            detail=content_type,
        )

    sdp_offer = (request.get_data(as_text=True) or "").strip()
    if not sdp_offer:
        return _error_response(
            "missing_sdp_offer",
            "Missing SDP offer body.",
            400,
            req_id,
        )

    session_cfg = realtime_session_config(
        "You are speaking with the user live over voice. If they speak in "
        "another language, respond in that same language unless asked to "
        "translate into a specific target language."
    )

    try:
        resp = requests.post(
            "https://api.openai.com/v1/realtime/calls",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            files={
                "sdp": (None, sdp_offer),
                "session": (None, json.dumps(session_cfg)),
            },
            timeout=35,
        )
    except requests.Timeout as exc:
        return _error_response(
            "openai_timeout",
            "OpenAI realtime signaling timed out.",
            504,
            req_id,
            detail=exc,
        )
    except requests.RequestException as exc:
        return _error_response(
            "openai_request_failed",
            "OpenAI realtime request failed.",
            502,
            req_id,
            detail=exc,
        )

    if resp.status_code >= 400:
        return _error_response(
            "openai_realtime_failed",
            f"Realtime signaling failed ({resp.status_code}).",
            resp.status_code,
            req_id,
            detail=resp.text,
        )

    sdp_response = Response(resp.text, status=resp.status_code, mimetype="application/sdp")
    sdp_response.headers["x-request-id"] = req_id
    sdp_response.headers["x-pj-contract-version"] = CONTRACT_VERSION
    return sdp_response


@app.route("/token", methods=["POST"])
def mint_realtime_token():
    """Mint a realtime client secret for browser fallback signaling."""
    req_id = _request_id()
    if "OPENAI_API_KEY" not in os.environ:
        return _error_response(
            "missing_openai_api_key",
            "OPENAI_API_KEY is not set.",
            500,
            req_id,
        )

    payload = request.get_json(force=True, silent=True)
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return _error_response(
            "invalid_json",
            "Expected JSON object for /token.",
            400,
            req_id,
        )

    session_cfg = realtime_session_config(
        "You are speaking with the user live over voice. If they speak in "
        "another language, respond in that same language unless asked to "
        "translate into a specific target language."
    )

    try:
        resp = requests.post(
            "https://api.openai.com/v1/realtime/client_secrets",
            headers={
                "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                "Content-Type": "application/json",
            },
            json={"session": session_cfg},
            timeout=20,
        )
    except requests.Timeout as exc:
        return _error_response(
            "openai_timeout",
            "OpenAI client secret minting timed out.",
            504,
            req_id,
            detail=exc,
        )
    except requests.RequestException as exc:
        return _error_response(
            "openai_request_failed",
            "OpenAI client secret request failed.",
            502,
            req_id,
            detail=exc,
        )

    if resp.status_code >= 400:
        return _error_response(
            "openai_client_secret_failed",
            f"Client secret minting failed ({resp.status_code}).",
            resp.status_code,
            req_id,
            detail=resp.text,
        )

    try:
        raw = resp.json()
    except ValueError:
        return _error_response(
            "invalid_client_secret_payload",
            "OpenAI returned non-JSON client secret payload.",
            502,
            req_id,
            detail=resp.text,
        )

    value = (
        ((raw.get("client_secret") or {}).get("value"))
        if isinstance(raw, dict)
        else None
    ) or (raw.get("value") if isinstance(raw, dict) else None)
    if not value:
        return _error_response(
            "invalid_client_secret_payload",
            "OpenAI client secret payload did not include a token value.",
            502,
            req_id,
            detail=raw,
        )

    return _json_response(
        {
            "ok": True,
            "client_secret": {
                "value": value,
                "expires_at": ((raw.get("client_secret") or {}).get("expires_at"))
                if isinstance(raw, dict) else None,
            },
            "tool_count": len(_function_tool_schemas()),
        },
        status=200,
        req_id=req_id,
    )


@app.route("/execute-tool", methods=["POST"])
def execute_tool():
    """Run a local skill on behalf of the browser client."""
    req_id = _request_id()
    auth_error = _check_bridge_auth(req_id)
    if auth_error:
        return auth_error
    payload = request.get_json(force=True, silent=True)
    if payload is None:
        return _error_response(
            "invalid_json",
            "Expected JSON body for /execute-tool.",
            400,
            req_id,
        )

    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return _error_response(
            "invalid_tool_name",
            "Tool name is required.",
            400,
            req_id,
        )

    arguments = payload.get("arguments") or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except json.JSONDecodeError as exc:
            return _error_response(
                "invalid_tool_arguments",
                "Tool arguments must be valid JSON.",
                400,
                req_id,
                detail=exc,
            )
    elif not isinstance(arguments, dict):
        return _error_response(
            "invalid_tool_arguments",
            "Tool arguments must be an object.",
            400,
            req_id,
        )

    try:
        result = dispatch_realtime_function(name, arguments)
    except ValueError as exc:
        return _error_response(
            "tool_dispatch_error",
            str(exc),
            400,
            req_id,
        )
    except Exception as exc:
        return _error_response(
            "tool_execution_error",
            "Tool execution failed.",
            500,
            req_id,
            detail=exc,
        )

    return _json_response(
        redact_server_paths(result), status=200, req_id=req_id
    )


@app.route("/webhook", methods=["POST"])
def sip_webhook():
    """Handle OpenAI realtime.call.incoming webhook events for SIP calls."""
    req_id = _request_id()
    event = request.get_json(force=True, silent=True) or {}
    event_type = event.get("type")

    if event_type != "realtime.call.incoming":
        return _json_response({"ok": True, "ignored_event_type": event_type}, req_id=req_id)

    call_id = event.get("data", {}).get("call_id")
    if not call_id:
        return _error_response(
            "missing_call_id",
            "Webhook payload missing data.call_id.",
            400,
            req_id,
        )
    if "OPENAI_API_KEY" not in os.environ:
        return _error_response(
            "missing_openai_api_key",
            "OPENAI_API_KEY is not set.",
            500,
            req_id,
        )

    session_cfg = realtime_session_config(
        "You are answering an inbound phone call on PJ's line. Greet the "
        "caller briefly, identify yourself as PJ, and help them."
    )
    try:
        requests.post(
            f"https://api.openai.com/v1/realtime/calls/{call_id}/accept",
            headers={
                "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                "Content-Type": "application/json",
            },
            json=session_cfg,
            timeout=30,
        )
    except requests.RequestException as exc:
        return _error_response(
            "openai_accept_failed",
            "Failed to accept inbound realtime call.",
            502,
            req_id,
            detail=exc,
        )

    return _json_response({"ok": True}, status=200, req_id=req_id)


if __name__ == "__main__":
    if "OPENAI_API_KEY" not in os.environ:
        raise SystemExit("OPENAI_API_KEY not set — source ~/.env first")
    app.run(host="0.0.0.0", port=3001)
