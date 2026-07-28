#!/usr/bin/env python3
"""PJ terminal client backed by the shared Responses runtime."""
import json
import sys

from openai import OpenAI

import chatlog
import skills
from responses_runtime import (
    build_tools,
    load_config,
    load_mcp_servers,
    load_state,
    save_state,
    send_message,
    send_structured_message,
)


def main():
    args = sys.argv[1:]
    if args and args[0] in ("voice", "--voice"):
        import voice
        voice.run(gate_enabled="--no-gate" not in args[1:], args=args[1:])
        return

    client = OpenAI()
    cfg = load_config()
    state = load_state()

    json_schema_path = None
    if args and args[0] == "--json":
        json_schema_path, *args = args[1:]

    if args:
        message = " ".join(args)
        session = chatlog.latest_session() or chatlog.new_session()
        state["previous_response_id"] = session.get("last_response_id")
        chatlog.record_turn(session, "user", message)
        if json_schema_path:
            with open(json_schema_path) as f:
                schema_cfg = json.load(f)
            print(f"{cfg['name']}: ", end="", flush=True)
            result = send_structured_message(
                client,
                cfg,
                state,
                message,
                schema_cfg["name"],
                schema_cfg["schema"],
                strict=schema_cfg.get("strict", True),
            )
            print("\n" + json.dumps(result, indent=2))
            chatlog.record_turn(
                session,
                "assistant",
                json.dumps(result),
                state.get("previous_response_id"),
            )
        else:
            print(f"{cfg['name']}: ", end="", flush=True)
            reply = send_message(client, cfg, state, message)
            chatlog.record_turn(
                session,
                "assistant",
                reply or "",
                state.get("previous_response_id"),
            )
        return

    print(
        f"Chatting with {cfg['name']} ({cfg['model']}). "
        "Palettes: / commands · # tools · % features · $ skills. "
        "Ctrl+C to exit.\n"
    )
    session = chatlog.latest_session() or chatlog.new_session()
    if session.get("title"):
        print(
            f"(Continuing: {session['title'][:60]} — /new for a fresh chat, "
            "/chats for others)\n"
        )
    state["previous_response_id"] = session.get("last_response_id")
    chatlog.setup_readline(skills.TOOL_SCHEMAS)

    try:
        while True:
            message = input("You: ").strip()
            if not message:
                continue
            handled, switched = chatlog.handle_command(
                message, session, skills.TOOL_SCHEMAS, cfg
            )
            if handled:
                if switched:
                    session = switched
                    state["previous_response_id"] = session.get(
                        "last_response_id"
                    )
                    save_state(state)
                continue
            chatlog.record_turn(session, "user", message)
            print(f"\n{cfg['name']}: ", end="", flush=True)
            reply = send_message(client, cfg, state, message)
            chatlog.record_turn(
                session,
                "assistant",
                reply or "",
                state.get("previous_response_id"),
            )
            print()
    except (KeyboardInterrupt, EOFError):
        print("\nBye!")


if __name__ == "__main__":
    main()
