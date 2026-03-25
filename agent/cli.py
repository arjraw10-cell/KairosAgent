from __future__ import annotations

import argparse
import json
import itertools
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.core import Agent
from agent.model import OpenAIChatModel
from agent.tooling import ToolContext, ToolRegistry
from agent import tools as builtin_tools
from agent.tools.browser_ops import close_browser_session


RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
FG_ORANGE = "\033[38;5;208m"
FG_GOLD = "\033[38;5;220m"
FG_RED = "\033[38;5;203m"
FG_MUTED = "\033[38;5;245m"
FG_SOFT = "\033[38;5;252m"


def _enable_ansi_on_windows() -> None:
    if os.name != "nt":
        return
    os.system("")


def _styled(text: str, *styles: str) -> str:
    return "".join(styles) + text + RESET


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise argparse.ArgumentTypeError("Expected `true` or `false`.")


def _provider_label(use_llama: bool) -> str:
    return "llama.cpp" if use_llama else "gemini"


def _sanitize_session_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return cleaned or "main"


def _session_path(agent_home_dir: Path, session_name: str) -> Path:
    return agent_home_dir / "chats" / f"{_sanitize_session_name(session_name)}.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, bytes):
        return value.hex()
    return value


def _save_session(agent: Agent, session_path: Path, args: argparse.Namespace) -> None:
    session_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_name": args.session,
        "provider": _provider_label(args.use_llama),
        "model": args.model,
        "max_steps": args.max_steps,
        "updated_at": _utc_now(),
        "messages": agent.messages,
        "token_totals": agent.total_usage,
    }
    session_path.write_text(
        json.dumps(_json_safe(payload), indent=2, ensure_ascii=True),
        encoding="utf-8",
    )


def _load_session(agent: Agent, session_path: Path) -> bool:
    if not session_path.exists():
        return False
    payload = json.loads(session_path.read_text(encoding="utf-8"))
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError(f"Session file is invalid: {session_path}")
    total_usage = payload.get("token_totals")
    if not isinstance(total_usage, dict):
        total_usage = None
    agent.load_session(messages, total_usage)
    return True


def _plain_text_reply(text: str) -> str:
    cleaned = text.replace("**", "").replace("__", "")
    return cleaned


def _divider(char: str = "-", width: int = 92) -> str:
    return _styled(char * width, FG_MUTED)


def _print_header(
    args: argparse.Namespace,
    workspace_dir: Path,
    agent_home_dir: Path,
    session_path: Path,
    resumed: bool,
) -> None:
    provider_label = _provider_label(args.use_llama)
    print()
    print(
        _styled(" Agent", BOLD, FG_GOLD)
        + _styled(" 2026.3", FG_MUTED)
        + _styled("  ", FG_MUTED)
        + _styled("|", FG_MUTED)
        + _styled(" Build useful things.", FG_ORANGE)
    )
    print()
    print(
        _styled(" local cli ", BOLD, FG_GOLD)
        + _styled("- ", FG_MUTED)
        + _styled(provider_label, FG_SOFT)
        + _styled(" - model ", FG_MUTED)
        + _styled(f"{args.model}", FG_SOFT)
        + _styled(f" - session {args.session}", FG_GOLD)
    )
    print(
        _styled(" workspace ", FG_MUTED)
        + _styled(str(workspace_dir), FG_SOFT)
    )
    print(
        _styled(" agent_home ", FG_MUTED)
        + _styled(str(agent_home_dir), FG_SOFT)
    )
    print(
        _styled(" max_steps ", FG_MUTED)
        + _styled(str(args.max_steps), FG_SOFT)
        + _styled(" | tools ", FG_MUTED)
        + _styled("loaded", FG_SOFT)
    )
    print(
        _styled(" session ", FG_MUTED)
        + _styled(args.session, FG_SOFT)
        + _styled(" | mode ", FG_MUTED)
        + _styled("resume" if resumed else "new", FG_SOFT)
    )
    print(
        _styled(" transcript ", FG_MUTED)
        + _styled(str(session_path), FG_SOFT)
    )
    print(_divider("="))
    print(_styled(" Type `exit` to quit.", FG_MUTED))
    print(_divider("="))
    print()


def _run_agent_with_spinner(agent: Agent, user_input: str) -> str:
    spinner_frames = itertools.cycle(["|", "/", "-", "\\"])
    stop_event = threading.Event()
    result: dict[str, Any] = {"reply": None, "error": None}

    def _worker() -> None:
        try:
            result["reply"] = agent.ask(user_input)
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc
        finally:
            stop_event.set()

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    status = _styled("agent", BOLD, FG_ORANGE) + _styled(" thinking ", FG_MUTED)
    while not stop_event.is_set():
        frame = next(spinner_frames)
        sys.stdout.write("\r" + status + _styled(frame, FG_GOLD))
        sys.stdout.flush()
        time.sleep(0.1)

    thread.join()
    sys.stdout.write("\r" + (" " * 48) + "\r")
    sys.stdout.flush()

    if result["error"] is not None:
        raise result["error"]
    return str(result["reply"] or "")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minimal Python tool-calling agent")
    parser.add_argument(
        "--use-llama",
        type=_parse_bool,
        default=False,
        help="Use local llama.cpp server when true, otherwise use Gemini.",
    )
    parser.add_argument(
        "--root", default=".", help="Root directory for file/shell tools"
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="Workspace directory for file/shell tools. Overrides --root when set.",
    )
    parser.add_argument(
        "--max-steps", type=int, default=20, help="Maximum tool-calling iterations"
    )
    parser.add_argument(
        "--session", default="main", help="Session name used for transcript persistence."
    )
    parser.add_argument(
        "--resume",
        type=_parse_bool,
        default=False,
        help="Resume an existing saved session when true.",
    )
    return parser


def main() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "python-dotenv is not installed. Run: pip install -r requirements.txt"
        ) from exc

    parser = build_parser()
    args = parser.parse_args()
    _enable_ansi_on_windows()

    agent_home_dir = Path(__file__).resolve().parents[1]
    workspace_value = args.workspace if args.workspace else args.root
    workspace_dir = Path(workspace_value).resolve()
    load_dotenv(agent_home_dir / ".env", override=False)
    if workspace_dir != agent_home_dir:
        load_dotenv(workspace_dir / ".env", override=False)

    provider = "llama_cpp" if args.use_llama else "gemini"
    if args.use_llama:
        model_name = os.getenv("LLAMA_CPP_MODEL", "local-model")
        base_url = os.getenv("LLAMA_CPP_BASE_URL", "http://127.0.0.1:8080/v1")
        api_key = os.getenv("LLAMA_CPP_API_KEY", "not-needed")
    else:
        model_name = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
        base_url = None
        api_key = None
    args.model = model_name

    context = ToolContext(
        root_dir=workspace_dir,
        agent_home_dir=agent_home_dir,
        runtime_state={},
    )
    registry = ToolRegistry(context)
    context.runtime_state["registry"] = registry
    registry.register_module(builtin_tools, origin="base")

    # Load dynamic skills from the skills/ directory
    from agent.tools.skill_ops import load_skills
    load_skills(registry, agent_home_dir / "skills")

    model = OpenAIChatModel(
        model_name=model_name,
        provider=provider,
        base_url=base_url,
        api_key=api_key,
    )
    agent = Agent(model=model, registry=registry, max_steps=args.max_steps)
    args.session = _sanitize_session_name(args.session)
    session_path = _session_path(agent_home_dir, args.session)
    resumed = False
    if args.resume:
        resumed = _load_session(agent, session_path)

    _print_header(args, workspace_dir, agent_home_dir, session_path, resumed)

    try:
        while True:
            try:
                user_input = input(_styled("you", BOLD, FG_GOLD) + _styled("> ", FG_MUTED)).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if user_input.lower() in {"exit", "quit"}:
                break
            if not user_input:
                continue

            try:
                reply = _run_agent_with_spinner(agent, user_input)
            except Exception as exc:
                print(_styled("agent-error", BOLD, FG_RED) + _styled("> ", FG_MUTED) + str(exc))
                print()
                continue
            _save_session(agent, session_path, args)
            print(_styled("agent", BOLD, FG_ORANGE) + _styled("> ", FG_MUTED) + _plain_text_reply(reply))
            usage = agent.last_usage
            total_usage = agent.total_usage
            print(
                _styled("tokens", FG_MUTED)
                + _styled("> ", FG_MUTED)
                + _styled(
                    (
                        f"turn total={usage['total_tokens']} "
                        f"(prompt={usage['prompt_tokens']}, completion={usage['completion_tokens']}, "
                        f"cached={usage.get('cached_prompt_tokens', 0)})"
                    ),
                    FG_SOFT,
                )
            )
            print(
                _styled("session", FG_MUTED)
                + _styled("> ", FG_MUTED)
                + _styled(
                    (
                        f"tokens total={total_usage['total_tokens']} "
                        f"(prompt={total_usage['prompt_tokens']}, completion={total_usage['completion_tokens']}, "
                        f"cached={total_usage.get('cached_prompt_tokens', 0)})"
                    ),
                    FG_SOFT,
                )
            )
            print()
    finally:
        close_browser_session(context)


if __name__ == "__main__":
    main()
