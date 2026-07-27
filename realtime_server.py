#!/usr/bin/env python3
"""
realtime_server.py — signaling/webhook server that connects PJ to voice.

Provides:
  POST /session   — browser WebRTC signaling (SDP offer in, SDP answer out).
                     Used by webrtc_client.html for mic-based voice chat.
  POST /webhook    — SIP webhook for inbound phone calls routed to PJ's
                     OpenAI project SIP URI (sip:<project-id>@sip.api.openai.com).

Both paths configure the same realtime session: PJ's model, instructions,
voice, server-side VAD, live input transcription, and live translation
guidance, plus the same function-calling "skills" used by pj.py.

Run:
    python3 realtime_server.py
Then open webrtc_client.html in a browser for mic-based voice chat, or
point a SIP trunk (e.g. Twilio Elastic SIP Trunking) at your OpenAI
project's SIP URI with this server's /webhook as the call-event target.
"""
import json
import os
from pathlib import Path

import requests
from flask import Flask, request, Response
from flask_cors import CORS

import skills
from realtime_config import realtime_session_config

BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__)
CORS(app)  # allow the local webrtc_client.html (opened via file://) to call us


@app.route("/session", methods=["POST"])
def webrtc_session():
    """Browser WebRTC signaling endpoint: exchanges SDP for an SDP answer."""
    sdp_offer = request.get_data(as_text=True)
    session_cfg = realtime_session_config(
        "You are speaking with the user live over voice. If they speak in "
        "another language, respond in that same language (live translation "
        "mode) unless asked to translate into a specific target language."
    )

    resp = requests.post(
        "https://api.openai.com/v1/realtime/calls",
        headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
        files={
            "sdp": (None, sdp_offer, "application/sdp"),
            "session": (None, json.dumps(session_cfg), "application/json"),
        },
        timeout=30,
    )
    if resp.status_code >= 400:
        print("REALTIME ERROR:", resp.status_code, resp.text, flush=True)
    return Response(resp.text, status=resp.status_code, mimetype="application/sdp")


@app.route("/execute-tool", methods=["POST"])
def execute_tool():
    """Runs a local skill on behalf of the browser client.

    The realtime session streams function-call requests to the browser over
    the WebRTC data channel (response.function_call_arguments.delta/.done).
    Browsers can't run PJ's Python skills directly, so webrtc_client.html
    posts the completed call here, we dispatch it through skills.py, and the
    browser feeds the result back into the realtime conversation as a
    function_call_output item.
    """
    payload = request.get_json(force=True, silent=True) or {}
    name = payload.get("name")
    arguments = payload.get("arguments") or {}
    if isinstance(arguments, str):
        arguments = json.loads(arguments or "{}")
    result = skills.dispatch(name, arguments)
    return Response(json.dumps(result), status=200, mimetype="application/json")


@app.route("/webhook", methods=["POST"])
def sip_webhook():
    """Handles OpenAI 'realtime.call.incoming' webhook events for SIP calls."""
    event = request.get_json(force=True, silent=True) or {}
    event_type = event.get("type")

    if event_type == "realtime.call.incoming":
        call_id = event["data"]["call_id"]
        session_cfg = realtime_session_config(
            "You are answering an inbound phone call on PJ's line. Greet "
            "the caller briefly, identify yourself as PJ, and help them."
        )
        requests.post(
            f"https://api.openai.com/v1/realtime/calls/{call_id}/accept",
            headers={
                "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                "Content-Type": "application/json",
            },
            json=session_cfg,
            timeout=30,
        )

    return Response(status=200)


if __name__ == "__main__":
    if "OPENAI_API_KEY" not in os.environ:
        raise SystemExit("OPENAI_API_KEY not set — source ~/.env first")
    app.run(host="0.0.0.0", port=3001)
