#!/usr/bin/env python3
"""PJ terminal client backed by the shared Responses runtime."""
import argparse
import json
import sys

from openai import OpenAI

import chatlog
import imageops
import promptops
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


def _run_image_cli(args):
    parser = argparse.ArgumentParser(prog="pj.py image")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    get_parser = commands.add_parser("get")
    get_parser.add_argument("asset_id")
    delete_parser = commands.add_parser("delete")
    delete_parser.add_argument("asset_id")
    controlled = commands.add_parser("controlled")
    controlled.add_argument("width", type=int)
    controlled.add_argument("height", type=int)
    controlled.add_argument("--title", default="")
    controlled.add_argument("--background", default="#101826")
    controlled.add_argument("--foreground", default="#f5f7fa")
    controlled.add_argument("--idempotency-key")
    generate = commands.add_parser("generate")
    generate.add_argument("prompt")
    generate.add_argument("--idempotency-key", required=True)
    generate.add_argument("--size", default="1024x1024")
    generate.add_argument("--quality", default="medium")
    inspect = commands.add_parser("inspect-package")
    inspect.add_argument("manifest")
    feedback = commands.add_parser("feedback")
    feedback.add_argument("asset_id")
    feedback.add_argument("rating", type=int)
    feedback.add_argument("--comments", default="")
    parsed = parser.parse_args(args)
    try:
        if parsed.command == "status":
            result = imageops.get_image_capability_status()
        elif parsed.command == "get":
            result = imageops.get_image_asset(parsed.asset_id)
        elif parsed.command == "delete":
            result = imageops.delete_image_asset(parsed.asset_id)
        elif parsed.command == "controlled":
            result = imageops.create_controlled_svg(
                width=parsed.width,
                height=parsed.height,
                title=parsed.title,
                background=parsed.background,
                foreground=parsed.foreground,
                idempotency_key=parsed.idempotency_key,
            )
        elif parsed.command == "generate":
            result = imageops.generate_image(
                parsed.prompt,
                size=parsed.size,
                quality=parsed.quality,
                idempotency_key=parsed.idempotency_key,
            )
        elif parsed.command == "inspect-package":
            result = imageops.inspect_training_package(parsed.manifest)
        else:
            result = imageops.record_image_feedback(
                parsed.asset_id,
                parsed.rating,
                comments=parsed.comments,
            )
    except imageops.ImageOpsError as exc:
        print(json.dumps(exc.as_dict(), indent=2), file=sys.stderr)
        raise SystemExit(2) from exc
    print(json.dumps(result, indent=2))


def _perfect_cli_prompt(client, cfg, message):
    try:
        return promptops.perfect_prompt(client, cfg, message, surface="cli")
    except promptops.PromptPerfectingError as exc:
        print(
            f"PJ prompt error [{exc.code}]: {exc}",
            file=sys.stderr,
        )
        return None


def main():
    args = sys.argv[1:]
    if args and args[0] in ("voice", "--voice"):
        import voice
        voice.run(gate_enabled="--no-gate" not in args[1:], args=args[1:])
        return
    if args and args[0] == "image":
        _run_image_cli(args[1:])
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
        perfected = _perfect_cli_prompt(client, cfg, message)
        if perfected is None:
            raise SystemExit(2)
        model_message = perfected["refined_prompt"]
        if json_schema_path:
            with open(json_schema_path) as f:
                schema_cfg = json.load(f)
            print(f"{cfg['name']}: ", end="", flush=True)
            result = send_structured_message(
                client,
                cfg,
                state,
                model_message,
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
            reply = send_message(client, cfg, state, model_message)
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
            perfected = _perfect_cli_prompt(client, cfg, message)
            if perfected is None:
                continue
            print(f"\n{cfg['name']}: ", end="", flush=True)
            reply = send_message(
                client, cfg, state, perfected["refined_prompt"]
            )
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
