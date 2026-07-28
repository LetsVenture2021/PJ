#!/usr/bin/env python3
"""
realtime_server.py — signaling/webhook server that connects PJ to voice.

Provides:
  POST /session    - browser WebRTC signaling (SDP offer in, SDP answer out)
  POST /execute-tool - executes local PJ tools for browser function calls
  POST /webhook    - SIP webhook for inbound phone calls
"""
import json
import os
from pathlib import Path
from uuid import uuid4

import requests
from flask import Flask, request, Response, send_from_directory
from flask_cors import CORS

import skills
from realtime_config import realtime_session_config

BASE_DIR = Path(__file__).resolve().parent
CONTRACT_VERSION = "2026-07-28.4"
MAX_ERROR_DETAIL_LENGTH = 320

app = Flask(__name__, static_folder=str(BASE_DIR / "assets"), static_url_path="/assets")
CORS(app)  # allow local browser origins when running on localhost


def _request_id():
    return request.headers.get("x-pj-client-request-id") or str(uuid4())


def _trim_detail(detail):
    if detail is None:
        return None
    compact = " ".join(str(detail).split()).strip()
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


def _check_bridge_auth(req_id):
    expected = (os.getenv("PJ_TOOL_BRIDGE_TOKEN") or "").strip()
    if not expected:
        return None
    provided = request.headers.get("Authorization") or ""
    if provided == f"Bearer {expected}":
        return None
    return _error_response(
        "bridge_auth_required",
        "Bridge authorization failed.",
        401,
        req_id,
    )


def _function_tool_schemas():
    tools = []
    for schema in skills.TOOL_SCHEMAS:
        if not isinstance(schema, dict):
            continue
        if schema.get("type") != "function":
            continue
        if not isinstance(schema.get("name"), str) or not schema["name"].strip():
            continue
        tools.append(schema)
    return tools


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
            "endpoints": ["/session", "/token", "/execute-tool", "/tool-schemas", "/webhook", "/health"],
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
        result = skills.dispatch(name, arguments)
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

    return _json_response(result, status=200, req_id=req_id)


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
