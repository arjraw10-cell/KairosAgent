from __future__ import annotations

import argparse
import itertools
import os
import sys
import threading
import time
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


def _divider(char: str = "-", width: int = 92) -> str:
    return _styled(char * width, FG_MUTED)


def _print_header(args: argparse.Namespace, root_dir: Path) -> None:
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
        + _styled(" - session main", FG_GOLD)
    )
    print(
        _styled(" root ", FG_MUTED)
        + _styled(str(root_dir), FG_SOFT)
    )
    print(
        _styled(" max_steps ", FG_MUTED)
        + _styled(str(args.max_steps), FG_SOFT)
        + _styled(" | tools ", FG_MUTED)
        + _styled("loaded", FG_SOFT)
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
        "--max-steps", type=int, default=20, help="Maximum tool-calling iterations"
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

    root_dir = Path(args.root).resolve()
    load_dotenv(root_dir / ".env", override=False)

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

    context = ToolContext(root_dir=root_dir, runtime_state={})
    registry = ToolRegistry(context)
    context.runtime_state["registry"] = registry
    registry.register_module(builtin_tools)

    model = OpenAIChatModel(
        model_name=model_name,
        provider=provider,
        base_url=base_url,
        api_key=api_key,
    )
    agent = Agent(model=model, registry=registry, max_steps=args.max_steps)

    _print_header(args, root_dir)

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
            print(_styled("agent", BOLD, FG_ORANGE) + _styled("> ", FG_MUTED) + reply)
            print()
    finally:
        close_browser_session(context)


if __name__ == "__main__":
    main()
