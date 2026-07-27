#!/usr/bin/env python3
"""
PJ — personal chief-of-staff assistant, built on OpenAI's Responses API.

Usage:
    python3 pj.py "your message here"
    python3 pj.py            # interactive chat loop
    python3 pj.py voice      # terminal voice mode (also: --voice)

Conversation continuity is kept via a stored previous_response_id so PJ
remembers context between calls without needing the (deprecated)
Assistants/Threads API.

Tool suite (Phase 1 + Phase 2):
    - code_interpreter   (sandboxed shell/python execution)
    - file_search        (vector store retrieval)
    - web_search         (live internet lookups)
    - image_generation   (create/edit images)
    - tool_search        (dynamic selection across large tool/MCP catalogs)
    - mcp                (remote MCP connectors, see mcp_servers.json)
    - computer_use       (GUI/browser automation, opt-in — see config.json)
    - local function tools defined in skills.py
"""
import json
import sys
from pathlib import Path

from openai import OpenAI

import chatlog
import skills

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state.json"
MCP_PATH = BASE_DIR / "mcp_servers.json"

# Models known to support the computer_use_preview tool. Update this set as
# OpenAI expands availability; PJ will pick it up automatically once your
# configured model (or account) is included.
COMPUTER_USE_MODELS = {"computer-use-preview"}


def load_config():
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    instructions_path = BASE_DIR / cfg["instructions_file"]
    with open(instructions_path) as f:
        cfg["instructions"] = f.read()
    return cfg


def load_state():
    if STATE_PATH.exists():
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"previous_response_id": None}


def save_state(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f)


def load_mcp_servers():
    if MCP_PATH.exists():
        with open(MCP_PATH) as f:
            return json.load(f)
    return []


def build_tools(cfg):
    tools = []

    # code_interpreter containers add startup latency to every call;
    # enable only when needed via config.
    if cfg.get("code_interpreter_enabled", True):
        tools.append({"type": "code_interpreter", "container": {"type": "auto"}})

    tools.append({"type": "web_search"})

    if cfg.get("image_generation_enabled", True):
        tools.append({"type": "image_generation"})

    if cfg.get("vector_store_id"):
        tools.append({
            "type": "file_search",
            "vector_store_ids": [cfg["vector_store_id"]],
        })

    # Computer-use requires OpenAI's dedicated computer-use-capable models
    # and account access; the flag stays on in config so this activates
    # automatically once available, but we guard against unsupported models
    # so PJ doesn't break in the meantime.
    if cfg.get("computer_use_enabled") and cfg["model"] in COMPUTER_USE_MODELS:
        tools.append({
            "type": "computer_use_preview",
            "display_width": 1280,
            "display_height": 800,
            "environment": "browser",
        })

    # Remote MCP connectors (configured per-server in mcp_servers.json).
    # Optional "headers" values support ${ENV_VAR} expansion so secrets
    # stay in the environment, not in the JSON file.
    import os
    for server in load_mcp_servers():
        if not server.get("enabled", True):
            continue
        entry = {
            "type": "mcp",
            "server_label": server["label"],
            "server_url": server["url"],
            "require_approval": server.get("require_approval", "always"),
        }
        headers = {}
        for k, v in server.get("headers", {}).items():
            expanded = os.path.expandvars(v)
            if "$" not in expanded:  # skip headers whose env var is unset
                headers[k] = expanded
        if headers:
            entry["headers"] = headers
        if server.get("allowed_tools"):
            entry["allowed_tools"] = server["allowed_tools"]
        tools.append(entry)

    # Local Python "skills" exposed as function-calling tools
    tools.extend(skills.TOOL_SCHEMAS)

    # tool_search lets the model dynamically pick from large tool catalogs
    # instead of loading every schema into context up front. It requires at
    # least one tool marked as deferred, so we defer the local skills'
    # schemas (the model discovers them via search when needed).
    if cfg.get("tool_search_enabled", True) and len(tools) > 8:
        for t in tools:
            if t.get("type") == "function":
                t["defer_loading"] = True
        tools.append({"type": "tool_search"})

    return tools


def stream_and_print(client, kwargs, *, echo=True):
    """Runs a streaming Responses API call, printing text deltas as they
    arrive and showing live progress for function calls as their arguments
    stream in. Returns the final completed Response object.

    Streaming function calls: `response.function_call_arguments.delta`
    events deliver a call's arguments incrementally (before the call is
    actually invoked), so we surface a one-line progress indicator per call
    the first time we see it, then let handle_function_calls run it once the
    full response has completed.
    """
    printed_text = False
    calls_announced = set()
    final_response = None

    for event in client.responses.create(**kwargs):
        et = event.type

        if et == "response.output_text.delta":
            if echo:
                print(event.delta, end="", flush=True)
            printed_text = True

        elif et == "response.function_call_arguments.delta":
            call_id = getattr(event, "call_id", None) or getattr(event, "item_id", None)
            if echo and call_id not in calls_announced:
                calls_announced.add(call_id)
                print("\n🔧 PJ is calling a function...", flush=True)

        elif et == "response.completed":
            final_response = event.response

        elif et == "response.error" or et == "error":
            raise RuntimeError(f"Streaming error: {getattr(event, 'message', event)}")

    if printed_text and echo:
        print()
    return final_response


def handle_function_calls(client, cfg, state, response):
    """Execute any local function ("skill") calls and feed results back."""
    tool_outputs = []
    for item in response.output:
        if item.type == "function_call":
            args = json.loads(item.arguments)
            print(f"   → {item.name}({json.dumps(args)})", flush=True)
            result = skills.dispatch(item.name, args)
            print(f"   ✅ {json.dumps(result)}", flush=True)
            tool_outputs.append({
                "type": "function_call_output",
                "call_id": item.call_id,
                "output": json.dumps(result),
            })

    if not tool_outputs:
        return response

    follow_up = stream_and_print(client, dict(
        model=cfg["model"],
        previous_response_id=response.id,
        input=tool_outputs,
        tools=build_tools(cfg),
        stream=True,
        **({"reasoning": {"effort": cfg["reasoning_effort"]}}
           if cfg.get("reasoning_effort") else {}),
    ))
    state["previous_response_id"] = follow_up.id
    save_state(state)
    return handle_function_calls(client, cfg, state, follow_up)


def send_message(client, cfg, state, message, text_format=None):
    """Sends a message with streaming text output and streaming function
    calls. `text_format`, if given, is a Responses API `text.format` object
    (e.g. {"type": "json_schema", "name": ..., "schema": ..., "strict": True})
    enabling streaming structured output: the model's JSON reply streams
    token-by-token exactly like free-form text, then gets parsed/validated
    once complete.
    """
    kwargs = dict(
        model=cfg["model"],
        instructions=cfg["instructions"],
        input=message,
        tools=build_tools(cfg),
        stream=True,
    )
    if cfg.get("reasoning_effort"):
        kwargs["reasoning"] = {"effort": cfg["reasoning_effort"]}
    if state.get("previous_response_id"):
        kwargs["previous_response_id"] = state["previous_response_id"]
    if text_format:
        kwargs["text"] = {"format": text_format}

    resp = stream_and_print(client, kwargs)
    state["previous_response_id"] = resp.id
    save_state(state)

    resp = handle_function_calls(client, cfg, state, resp)
    return resp.output_text


def send_structured_message(client, cfg, state, message, schema_name, json_schema, strict=True):
    """Convenience wrapper for streaming structured output: streams the raw
    JSON as PJ generates it, then returns it parsed as a Python object.
    """
    text_format = {
        "type": "json_schema",
        "name": schema_name,
        "schema": json_schema,
        "strict": strict,
    }
    raw = send_message(client, cfg, state, message, text_format=text_format)
    return json.loads(raw)


def main():
    args = sys.argv[1:]
    if args and args[0] in ("voice", "--voice"):
        # Terminal voice mode: talk to PJ over mic/speakers, no browser.
        # --no-gate disables echo suppression; --meter calibrates the mic.
        import voice
        voice.run(gate_enabled="--no-gate" not in args[1:], args=args[1:])
        return

    client = OpenAI()
    cfg = load_config()
    state = load_state()

    json_schema_path = None
    if args and args[0] == "--json":
        # python3 pj.py --json path/to/schema.json "your message"
        json_schema_path, *args = args[1:]

    if args:
        message = " ".join(args)
        session = chatlog.latest_session() or chatlog.new_session()
        state["previous_response_id"] = session.get("last_response_id")
        chatlog.record_turn(session, "user", message)
        if json_schema_path:
            with open(json_schema_path) as f:
                schema_cfg = json.load(f)
            print(f"{cfg['name']}: ", end="", flush=True)  # streamed JSON follows
            result = send_structured_message(
                client, cfg, state, message,
                schema_cfg["name"], schema_cfg["schema"],
                strict=schema_cfg.get("strict", True),
            )
            print("\n" + json.dumps(result, indent=2))
            chatlog.record_turn(session, "assistant", json.dumps(result),
                                state.get("previous_response_id"))
        else:
            print(f"{cfg['name']}: ", end="", flush=True)
            reply = send_message(client, cfg, state, message)
            chatlog.record_turn(session, "assistant", reply or "",
                                state.get("previous_response_id"))
        return

    print(f"Chatting with {cfg['name']} ({cfg['model']}). "
          f"Palettes: / commands · # tools · % features · $ skills. "
          f"Ctrl+C to exit.\n")

    # Resume the most recent chat session (model context follows via its
    # stored last_response_id); /new starts a fresh one, /chats lists all.
    session = chatlog.latest_session() or chatlog.new_session()
    if session.get("title"):
        print(f"(Continuing: {session['title'][:60]} — /new for a fresh chat, "
              f"/chats for others)\n")
    state["previous_response_id"] = session.get("last_response_id")
    chatlog.setup_readline(skills.TOOL_SCHEMAS)

    try:
        while True:
            message = input("You: ").strip()
            if not message:
                continue
            handled, switched = chatlog.handle_command(
                message, session, skills.TOOL_SCHEMAS, cfg)
            if handled:
                if switched:
                    session = switched
                    state["previous_response_id"] = session.get(
                        "last_response_id")
                    save_state(state)
                continue
            chatlog.record_turn(session, "user", message)
            print(f"\n{cfg['name']}: ", end="", flush=True)
            reply = send_message(client, cfg, state, message)
            chatlog.record_turn(session, "assistant", reply or "",
                                state.get("previous_response_id"))
            print()
    except (KeyboardInterrupt, EOFError):
        print("\nBye!")


if __name__ == "__main__":
    main()
