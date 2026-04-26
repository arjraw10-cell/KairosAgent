from __future__ import annotations

import argparse
import sys
import json
import threading
import time
import os
from datetime import datetime, timezone

import keyboard

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich.markdown import Markdown
from rich.table import Table

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.styles import Style as PtStyle
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.shortcuts import radiolist_dialog

console = Console()

GATEWAY_URL = "http://127.0.0.1:8000"

KAIROS_ASCII = """[bold dark_orange]
██╗  ██╗ █████╗ ██╗██████╗  ██████╗ ███████╗
██║ ██╔╝██╔══██╗██║██╔══██╗██╔═══██╗██╔════╝
█████╔╝ ███████║██║██████╔╝██║   ██║███████╗
██╔═██╗ ██╔══██║██║██╔══██╗██║   ██║╚════██║
██║  ██╗██║  ██║██║██║  ██║╚██████╔╝███████║
╚═╝  ╚═╝╚═╝  ╚═╝╚═╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝
[/bold dark_orange]"""


class KawaiiSpinner:
    FACES = ["(・_・)", "(o_o)", "(O_O)", "(^o^)", "(^-^*)", "(>_<)"]

    def __init__(self, console: Console):
        self.console = console
        self._status = "Thinking..."
        self._running = False
        self._thread = None

    def start(self, initial_status: str = "Thinking..."):
        self._status = initial_status
        self._running = True
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def update(self, status: str):
        self._status = status

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join()

    def _spin(self):
        idx = 0
        with Live(console=self.console, refresh_per_second=10, transient=True) as live:
            while self._running:
                face = self.FACES[idx % len(self.FACES)]
                idx += 1
                live.update(Text(f" {face} {self._status}", style="bold cyan"))
                time.sleep(0.15)


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true": return True
    if normalized == "false": return False
    raise argparse.ArgumentTypeError("Expected `true` or `false`.")


def print_header(config: dict, session_name: str, mode: str, resumed: bool) -> None:
    console.print(KAIROS_ASCII, highlight=False)
    console.print("[bold gold1]✦ A Minimal Personal Agent Framework ✦[/bold gold1]\n")
    
    details = Text()
    details.append("Provider:  ", style="#8a8a8a")
    details.append(f"{config.get('provider', 'unknown')}\n", style="green")
    details.append("Model:     ", style="#8a8a8a")
    details.append(f"{config.get('model', 'unknown')}\n", style="#cccccc")
    details.append("Workspace: ", style="#8a8a8a")
    details.append(f"{config.get('workspace', 'unknown')}\n", style="#cccccc")
    details.append("State:     ", style="#8a8a8a")
    details.append(f"{mode} / {'resume' if resumed else 'new'} (session: {session_name})\n", style="#cccccc")
    
    panel = Panel(details, title="[cyan]Kairos v0.1.0[/cyan]", border_style="#444444", expand=False)
    console.print(panel)
    console.print("[dim]● ready - connected to gateway[/dim]\n")


def show_help():
    table = Table(title="Kairos Commands", border_style="dim", box=None)
    table.add_column("Command", style="bold gold1")
    table.add_column("Description", style="dim")
    
    table.add_row("/help", "Show this help menu.")
    table.add_row("/clear", "Clear the terminal screen.")
    table.add_row("/exit", "Exit the CLI.")
    table.add_row("/new", "Start a completely fresh session.")
    table.add_row("/resume", "Choose a saved session to resume.")
    table.add_row("/model <name>", "Switch the active model and update settings.json.")
    table.add_row("/mode", "Choose personalized or unbiased mode.")
    table.add_row("/session", "Show token usage statistics for the current run.")
    
    console.print(Panel(table, border_style="#444444", expand=False))


def _close_remote_session(client: httpx.Client, session_name: str, mode: str) -> None:
    payload = {
        "address": {"platform": "cli", "session": session_name},
        "text": "",
        "resume": True,
        "mode": mode,
    }
    try:
        client.post(f"{GATEWAY_URL}/sessions/close", json=payload)
    except Exception:
        pass


def _format_age(timestamp: float | int | None) -> str:
    if not timestamp:
        return "-"
    seconds = max(0, int(datetime.now(timezone.utc).timestamp() - float(timestamp)))
    units = [
        ("year", 365 * 24 * 60 * 60),
        ("month", 30 * 24 * 60 * 60),
        ("day", 24 * 60 * 60),
        ("hour", 60 * 60),
        ("minute", 60),
    ]
    for name, size in units:
        value = seconds // size
        if value:
            suffix = "" if value == 1 else "s"
            return f"{value} {name}{suffix} ago"
    return "just now"


def _clip(value: str, width: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= width:
        return text.ljust(width)
    return text[: max(0, width - 3)] + "..."


def choose_mode(current_mode: str) -> str | None:
    return radiolist_dialog(
        title="Choose Mode",
        text="Select how Kairos should behave for this session.",
        values=[
            ("personalized", "personalized  Use memory, preferences, and identity"),
            ("unbiased", "unbiased      Use a neutral prompt with fewer personal assumptions"),
        ],
        default=current_mode,
    ).run()


def choose_session(client: httpx.Client) -> str | None:
    try:
        resp = client.get(f"{GATEWAY_URL}/sessions")
        resp.raise_for_status()
        sessions = resp.json().get("sessions", [])
    except Exception as exc:
        console.print(f"[bold red]Could not load sessions:[/bold red] {exc}")
        return None

    if not sessions:
        console.print("[bold red]No saved sessions found.[/bold red]")
        return None

    values = []
    for item in sessions[:50]:
        session_name = str(item.get("session_name", ""))
        updated = _format_age(item.get("updated_ts"))
        mode = _clip(str(item.get("mode", "")) or "-", 12)
        preview = _clip(str(item.get("preview", "")) or "(empty session)", 54)
        label = f"{updated:<15} {mode} {preview}  [{session_name}]"
        values.append((session_name, label))

    return radiolist_dialog(
        title="Resume Session",
        text="Updated         Mode         Conversation",
        values=values,
        default=values[0][0],
    ).run()


def run_chat(session_name: str, mode: str, resume: bool):
    completer = WordCompleter([
        '/help', '/exit', '/quit', '/clear', '/mode personalized', '/mode unbiased', '/model', '/new', '/resume', '/session'
    ], ignore_case=True)
    
    pt_style = PtStyle.from_dict({
        'prompt': 'bold #ffd700',
        'bottom-toolbar': '#666666',
    })
    
    session = PromptSession(
        history=InMemoryHistory(),
        completer=completer,
        auto_suggest=AutoSuggestFromHistory(),
        style=pt_style
    )

    last_turn_usage = {}
    last_session_usage = {}
    current_mode = mode
    current_session = session_name
    should_resume = resume

    with httpx.Client(timeout=None) as client:
        while True:
            try:
                user_input = session.prompt(
                    HTML('<prompt>❯ </prompt>'), 
                    bottom_toolbar='Type /help for commands. Press Esc+Enter for multiline.',
                    multiline=False
                ).strip()
            except (EOFError, KeyboardInterrupt):
                _close_remote_session(client, current_session, current_mode)
                console.print()
                break

            if not user_input: 
                continue
            text_lower = user_input.lower()
            
            if text_lower in {"/exit", "/quit", "exit", "quit"}: 
                _close_remote_session(client, current_session, current_mode)
                break
            elif text_lower == "/help":
                show_help()
                continue
            elif text_lower == "/clear":
                console.clear()
                continue
            elif text_lower.startswith("/model"):
                parts = user_input.split(" ", 1)
                if len(parts) < 2:
                    console.print("[dim]Usage: /model <model_name>[/dim]")
                else:
                    new_model = parts[1].strip()
                    try:
                        resp = client.post(f"{GATEWAY_URL}/model", json={"model": new_model})
                        if resp.status_code == 200:
                            console.print(f"[bold green]Model switched to {new_model}[/bold green]")
                        else:
                            console.print(f"[bold red]Failed to switch model: {resp.text}[/bold red]")
                    except Exception as e:
                        console.print(f"[bold red]Error:[/bold red] {e}")
                continue
            elif text_lower.startswith("/mode"):
                parts = user_input.split(" ", 1)
                if len(parts) < 2:
                    target_mode = choose_mode(current_mode)
                else:
                    target_mode = parts[1].strip().lower()
                if target_mode is None:
                    continue
                if target_mode in ["personalized", "unbiased"]:
                    _close_remote_session(client, current_session, current_mode)
                    current_mode = target_mode
                    console.print(f"[bold green]Mode switched to {current_mode}[/bold green]")
                else:
                    console.print("[bold red]Invalid mode. Use 'personalized' or 'unbiased'.[/bold red]")
                continue
            elif text_lower.startswith("/new"):
                _close_remote_session(client, current_session, current_mode)
                should_resume = False
                try:
                    current_session = client.get(f"{GATEWAY_URL}/sessions/next").json()['session']
                    console.print(f"[bold green]Started new session:[/bold green] {current_session}")
                except Exception as e:
                    console.print(f"[bold red]Error connecting to gateway: {e}[/bold red]")
                continue
            elif text_lower.startswith("/resume"):
                _close_remote_session(client, current_session, current_mode)
                parts = user_input.split(" ", 1)
                should_resume = True
                try:
                    if len(parts) > 1:
                        target = parts[1].strip()
                        current_session = target
                    else:
                        target = choose_session(client)
                        if not target:
                            continue
                        current_session = target
                    console.print(f"[bold green]Ready to resume session:[/bold green] {current_session}")
                except Exception as e:
                    console.print(f"[bold red]Error connecting to gateway: {e}[/bold red]")
                continue
            elif text_lower == "/session":
                table = Table(title="Token Usage Statistics", border_style="#444444", box=None)
                table.add_column("Category", style="bold cyan")
                table.add_column("Turn Total", justify="right", style="dark_cyan")
                table.add_column("Session Total", justify="right", style="dark_cyan")
                
                table.add_row(
                    "Prompt (Input)", 
                    str(last_turn_usage.get('prompt_tokens', 0)), 
                    str(last_session_usage.get('prompt_tokens', 0))
                )
                table.add_row(
                    "Completion (Output)", 
                    str(last_turn_usage.get('completion_tokens', 0)), 
                    str(last_session_usage.get('completion_tokens', 0))
                )
                table.add_row(
                    "Cached Prompt", 
                    str(last_turn_usage.get('cached_prompt_tokens', 0)), 
                    str(last_session_usage.get('cached_prompt_tokens', 0)),
                    style="dim"
                )
                table.add_section()
                table.add_row(
                    "TOTAL", 
                    str(last_turn_usage.get('total_tokens', 0)), 
                    str(last_session_usage.get('total_tokens', 0)),
                    style="bold gold1"
                )
                
                console.print(Panel(table, title="[cyan]Session Tracking[/cyan]", border_style="#444444", expand=False))
                continue
            
            # If it's a regular message
            payload = {
                "address": {"platform": "cli", "session": current_session},
                "text": user_input,
                "resume": should_resume,
                "mode": current_mode
            }
            
            spinner = KawaiiSpinner(console)
            spinner.start("Thinking...")
            
            final_response = None
            try:
                with client.stream("POST", f"{GATEWAY_URL}/handle", json=payload) as response:
                    for line in response.iter_lines():
                        if not line.startswith("data: "): 
                            continue
                        
                        event = json.loads(line[6:])
                        ev_type = event.get("type")
                        data = event.get("data")
                        
                        if ev_type == "tool_start":
                            tool_name = data.get('tool', 'tool')
                            spinner.update(f"Using {tool_name}...")
                        elif ev_type == "final_response":
                            final_response = data
                        
                        # Check for interruption (Escape key)
                        if keyboard.is_pressed('esc'):
                            console.print("\n[bold red]Interrupted by user (Esc).[/bold red]")
                            response.close()
                            break
            except Exception as e:
                spinner.stop()
                console.print(f"[bold red]Gateway error:[/bold red] {e}")
                continue
                
            spinner.stop()
            
            if final_response:
                reply_text = final_response.get('text', '')
                console.print(Panel(
                    Markdown(reply_text), 
                    title="[bold dark_orange]Kairos[/bold dark_orange]", 
                    border_style="#444444", 
                    title_align="left"
                ))
                
                last_turn_usage = final_response.get('usage', {})
                last_session_usage = final_response.get('total_usage', {})
                
                # After successfully completing one turn, follow-ups are resumes
                should_resume = True
                current_session = final_response.get('session_name', current_session)
            else:
                console.print("[bold red]Error: No final response from gateway.[/bold red]")


def main():
    parser = argparse.ArgumentParser(description="Kairos CLI Client")
    parser.add_argument("--session", default=None, help="Session identifier for resuming conversation.")
    parser.add_argument("--resume", type=_parse_bool, default=False, help="Resume the latest session.")
    parser.add_argument("--mode", choices=["personalized", "unbiased"], default="personalized", help="The mode of the agent.")
    args = parser.parse_args()

    try:
        with httpx.Client() as client:
            config = client.get(f"{GATEWAY_URL}/config").json()
            if args.session is None:
                if args.resume:
                    res = client.get(f"{GATEWAY_URL}/sessions/latest").json()
                    if res.get('session'):
                        args.session = res['session']
                    else:
                        args.session = client.get(f"{GATEWAY_URL}/sessions/next").json()['session']
                else:
                    args.session = client.get(f"{GATEWAY_URL}/sessions/next").json()['session']
    except Exception:
        console.print(f"[bold red]Could not connect to Kairos Gateway at {GATEWAY_URL}. Is it running?[/bold red]")
        console.print("[dim]Run 'python -m agent.gateway' in another terminal.[/dim]")
        return

    print_header(config, args.session, args.mode, args.resume)
    run_chat(args.session, args.mode, args.resume)


if __name__ == "__main__":
    main()
