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
from ops.shared.logging import (
    bind_log_context,
    configure_logging,
    new_correlation_id,
)
from responses_runtime import (
    build_tools,
    capability_manifest,
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


def _show_workbench(cfg, *, as_json=False):
    manifest = capability_manifest(cfg)
    if as_json:
        print(json.dumps(manifest["experience"], indent=2))
    else:
        print(_render_workbench(manifest))


def _render_workbench(manifest):
    experience = manifest.get("experience") or {}
    workflows = experience.get("workflows") or []
    active = sum(1 for workflow in workflows if workflow.get("status") == "active")
    lines = [
        "",
        "  WORKBENCH   (launch: /workbench <workflow> <task>)",
        f"  {active}/{len(workflows)} workflows active"
        f"   ·   default: {experience.get('default_mode', 'full_power_text')}",
    ]
    if not workflows:
        lines.append("   No workflow manifest is available.")
    for workflow in workflows:
        workflow_id = str(workflow.get("id") or "")
        label = str(workflow.get("label") or workflow_id or "Workflow")
        status = str(workflow.get("status") or "unavailable").upper()
        tools = workflow.get("tools") or []
        tool_text = ", ".join(str(tool) for tool in tools[:4])
        if len(tools) > 4:
            tool_text += f", +{len(tools) - 4}"
        lines.append(f"\n   {label:<12} [{status}]  {workflow_id}")
        if tool_text:
            lines.append(f"     {tool_text}")
    boundaries = experience.get("approval_boundaries") or []
    if boundaries:
        lines.append(
            "\n  Approval-gated: "
            + ", ".join(str(boundary).replace("_", " ") for boundary in boundaries)
        )
    lines.append("")
    return "\n".join(lines)


def _resolve_workbench_launch(line, cfg):
    stripped = line.strip()
    command = stripped.split(maxsplit=1)[0].lower() if stripped else ""
    if command != "/workbench":
        return None
    parts = stripped.split(maxsplit=2)
    if len(parts) == 1:
        return None
    manifest = capability_manifest(cfg)
    workflows = (manifest.get("experience") or {}).get("workflows") or []
    selected = parts[1].casefold()
    workflow = next(
        (
            item
            for item in workflows
            if selected
            in {
                str(item.get("id") or "").casefold(),
                str(item.get("label") or "").casefold(),
            }
        ),
        None,
    )
    if workflow is None:
        available = ", ".join(str(item.get("id")) for item in workflows)
        return {"error": f"Unknown workflow '{parts[1]}'. Available: {available}"}
    if len(parts) < 3 or not parts[2].strip():
        return {
            "error": (f"Add a task after '{parts[1]}'. Usage: /workbench {workflow['id']} <task>")
        }
    task = parts[2].strip()
    launch_prompt = str(workflow.get("launch_prompt") or "").strip()
    model_prompt = f"{launch_prompt}\n\nUser task:\n{task}" if launch_prompt else task
    return {
        "workflow": workflow,
        "user_prompt": task,
        "model_prompt": model_prompt,
    }


def main():
    configure_logging()
    bind_log_context(request_id=new_correlation_id())
    args = sys.argv[1:]
    if args and args[0] in ("voice", "--voice"):
        import voice

        voice.run(gate_enabled="--no-gate" not in args[1:], args=args[1:])
        return
    if args and args[0] == "image":
        _run_image_cli(args[1:])
        return

    cfg = load_config()
    workbench_launch = None
    if args and args[0] == "workbench":
        if len(args) == 1 or args[1:] == ["--json"]:
            _show_workbench(cfg, as_json=args[1:] == ["--json"])
            return
        workbench_launch = _resolve_workbench_launch(
            "/workbench " + " ".join(args[1:]),
            cfg,
        )
        if workbench_launch.get("error"):
            print(f"PJ workbench error: {workbench_launch['error']}", file=sys.stderr)
            raise SystemExit(2)
        args = [workbench_launch["user_prompt"]]

    client = OpenAI()
    state = load_state()

    json_schema_path = None
    if args and args[0] == "--json":
        json_schema_path, *args = args[1:]

    if args:
        message = " ".join(args)
        session = chatlog.latest_session() or chatlog.new_session()
        bind_log_context(session_id=session["id"])
        state["previous_response_id"] = session.get("last_response_id")
        chatlog.record_turn(session, "user", message)
        prompt_input = workbench_launch["model_prompt"] if workbench_launch else message
        perfected = _perfect_cli_prompt(client, cfg, prompt_input)
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
        "Workbench: /workbench. "
        "Ctrl+C to exit.\n"
    )
    session = chatlog.latest_session() or chatlog.new_session()
    bind_log_context(session_id=session["id"])
    if session.get("title"):
        print(f"(Continuing: {session['title'][:60]} — /new for a fresh chat, /chats for others)\n")
    state["previous_response_id"] = session.get("last_response_id")
    chatlog.setup_readline(skills.TOOL_SCHEMAS)

    try:
        while True:
            message = input("You: ").strip()
            if not message:
                continue
            bind_log_context(request_id=new_correlation_id())
            if message == "/workbench":
                _show_workbench(cfg)
                continue
            workbench_launch = _resolve_workbench_launch(message, cfg)
            if workbench_launch is not None:
                if workbench_launch.get("error"):
                    print(f"  {workbench_launch['error']}")
                    continue
                print(f"  Launching {workbench_launch['workflow']['label']} workflow.")
                message = workbench_launch["user_prompt"]
            handled, switched = chatlog.handle_command(message, session, skills.TOOL_SCHEMAS, cfg)
            if handled:
                if switched:
                    session = switched
                    bind_log_context(session_id=session["id"])
                    state["previous_response_id"] = session.get("last_response_id")
                    save_state(state)
                continue
            chatlog.record_turn(session, "user", message)
            prompt_input = workbench_launch["model_prompt"] if workbench_launch else message
            perfected = _perfect_cli_prompt(client, cfg, prompt_input)
            if perfected is None:
                continue
            print(f"\n{cfg['name']}: ", end="", flush=True)
            reply = send_message(client, cfg, state, perfected["refined_prompt"])
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
