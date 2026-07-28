#!/usr/bin/env python3
"""
realtime_config.py — shared OpenAI Realtime session configuration for PJ.

Used by both voice front-ends:
  - realtime_server.py  (browser WebRTC signaling + SIP webhook)
  - voice.py            (terminal voice mode, `pj voice`)

Both configure the same realtime session: PJ's model, instructions, voice,
server-side VAD, live input transcription, and the same function-calling
"skills" used by pj.py.
"""
import os

import skills
from responses_runtime import ADVANCED_DELEGATION_TOOL, load_instructions

REALTIME_MODEL = os.getenv("PJ_REALTIME_MODEL", "gpt-realtime-2.1")
VOICE = os.getenv("PJ_REALTIME_VOICE", "marin")
REALTIME_EXCLUDED_TOOL_NAMES = {
    "approve_codeops_task",
    "create_skill",
    "learn_from_vector_store",
    "run_codeops_validation",
    "run_shortcut",
    "sync_vector_store",
}


def load_pj_instructions():
    return load_instructions()


def realtime_tool_schemas():
    return [
        schema
        for schema in skills.TOOL_SCHEMAS
        if schema.get("name") not in REALTIME_EXCLUDED_TOOL_NAMES
    ] + [ADVANCED_DELEGATION_TOOL]


def realtime_session_config(extra_instructions=""):
    _, instructions = load_pj_instructions()
    return {
        "type": "realtime",
        "model": REALTIME_MODEL,
        "instructions": instructions + "\n\n" + extra_instructions,
        "audio": {
            "input": {
                "transcription": {"model": "gpt-4o-transcribe"},
                "turn_detection": {
                    "type": "server_vad",  # server-side voice activity detection
                    "silence_duration_ms": 500,
                },
            },
            "output": {"voice": VOICE},
        },
        "tools": realtime_tool_schemas(),
    }
