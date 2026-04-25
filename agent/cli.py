from __future__ import annotations

import argparse
import sys
import json
import threading
import time
import os

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
    table.add_row("/resume <id>", "Resume a specific session (leave blank for latest).")
    table.add_row("/model <name>", "Switch the active model and update settings.json.")
    table.add_row("/mode <type>", "Switch between 'personalized' and 'unbiased'.")
    table.add_row("/session", "Show token usage statistics for the current run.")
    
    console.print(Panel(table, border_style="#444444", expand=False))


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
                console.print()
                break

            if not user_input: 
                continue
            text_lower = user_input.lower()
            
            if text_lower in {"/exit", "/quit", "exit", "quit"}: 
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
                    console.print(f"[dim]Current Mode: {current_mode}. Use /mode personalized or /mode unbiased[/dim]")
                else:
                    target_mode = parts[1].strip().lower()
                    if target_mode in ["personalized", "unbiased"]:
                        current_mode = target_mode
                        console.print(f"[bold green]Mode switched to {current_mode}[/bold green]")
                    else:
                        console.print("[bold red]Invalid mode. Use 'personalized' or 'unbiased'.[/bold red]")
                continue
            elif text_lower.startswith("/new"):
                should_resume = False
                try:
                    current_session = client.get(f"{GATEWAY_URL}/sessions/next").json()['session']
                    console.print(f"[bold green]Started new session:[/bold green] {current_session}")
                except Exception as e:
                    console.print(f"[bold red]Error connecting to gateway: {e}[/bold red]")
                continue
            elif text_lower.startswith("/resume"):
                parts = user_input.split(" ", 1)
                should_resume = True
                try:
                    if len(parts) > 1:
                        target = parts[1].strip()
                        # Use address.session logic on gateway to resolve/fuzzy match
                        payload = {"address": {"platform": "cli", "session": target}, "text": "ping", "resume": True}
                        # We don't want to actually send a message, we just want to resolve the name.
                        # But we don't have a lookup-only endpoint yet.
                        # Actually, let's just update current_session and let the next message handle it.
                        # However, to be safe, we can check if it exists via a small GET or just trust the next turn.
                        current_session = target
                    else:
                        resp = client.get(f"{GATEWAY_URL}/sessions/latest").json()
                        current_session = resp.get('session')
                        if not current_session:
                            console.print("[bold red]No previous session found.[/bold red]")
                            continue
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
