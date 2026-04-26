from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table


ROOT = Path(__file__).resolve().parent
SETTINGS_PATH = ROOT / "settings.json"
ENV_PATH = ROOT / ".env"

PROVIDERS = {
    "1": {
        "name": "gemini",
        "label": "Google Gemini",
        "model": "gemini-3-flash-preview",
        "key_env": "GEMINI_API_KEY",
        "needs_base_url": False,
    },
    "2": {
        "name": "openai",
        "label": "OpenAI",
        "model": "gpt-4.1-mini",
        "key_env": "OPENAI_API_KEY",
        "needs_base_url": False,
    },
    "3": {
        "name": "anthropic",
        "label": "Anthropic Claude",
        "model": "claude-sonnet-4-20250514",
        "key_env": "ANTHROPIC_API_KEY",
        "base_url": "https://api.anthropic.com/v1/",
        "needs_base_url": False,
    },
    "4": {
        "name": "openai_compatible",
        "label": "OpenAI-compatible endpoint",
        "model": "local-model",
        "key_env": "OPENAI_COMPATIBLE_API_KEY",
        "needs_base_url": True,
    },
    "5": {
        "name": "llama_cpp",
        "label": "llama.cpp local server",
        "model": "local-model",
        "key_env": "LLAMA_CPP_API_KEY",
        "base_url": "http://127.0.0.1:8080/v1",
        "needs_base_url": False,
        "default_key": "not-needed",
    },
}


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env(path: Path, values: dict[str, str]) -> None:
    lines = [f'{key}="{value}"' for key, value in sorted(values.items()) if value != ""]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def prompt_provider(console: Console) -> dict[str, str]:
    table = Table(title="Provider", show_lines=False)
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Provider")
    table.add_column("Default model", style="dim")
    for key, item in PROVIDERS.items():
        table.add_row(key, item["label"], item["model"])
    console.print(table)

    choice = Prompt.ask("Choose a provider", choices=list(PROVIDERS), default="1")
    provider = dict(PROVIDERS[choice])
    provider["model"] = Prompt.ask("Model", default=provider["model"])
    return provider


def configure_provider(console: Console, env_values: dict[str, str]) -> dict[str, str | None]:
    provider = prompt_provider(console)
    key_env = provider["key_env"]
    current_key = os.getenv(key_env) or env_values.get(key_env) or provider.get("default_key", "")

    if current_key:
        if Confirm.ask(f"Keep existing {key_env}?", default=True):
            api_key = current_key
        else:
            api_key = Prompt.ask(f"{key_env}", password=True)
    else:
        api_key = Prompt.ask(f"{key_env}", password=True)

    env_values[key_env] = api_key
    env_values["API_KEY"] = api_key

    base_url = provider.get("base_url")
    if provider.get("needs_base_url"):
        base_url = Prompt.ask(
            "OpenAI-compatible base URL",
            default=env_values.get("OPENAI_COMPATIBLE_BASE_URL", "http://127.0.0.1:8000/v1"),
        )
        env_values["OPENAI_COMPATIBLE_BASE_URL"] = base_url
    elif provider["name"] == "anthropic":
        env_values["ANTHROPIC_BASE_URL"] = str(base_url)
    elif provider["name"] == "llama_cpp":
        env_values["LLAMA_CPP_BASE_URL"] = str(base_url)
        env_values["LLAMA_CPP_MODEL"] = provider["model"]

    return {
        "provider": provider["name"],
        "model": provider["model"],
        "base_url": base_url,
    }


def configure_chrome(console: Console, env_values: dict[str, str]) -> None:
    console.print(Panel("Browser tools use Playwright Chromium by default. You can install it now or point Kairos at an existing Chrome.", title="Chrome"))
    choice = Prompt.ask(
        "Chrome setup",
        choices=["playwright", "existing", "skip"],
        default="playwright",
    )

    if choice == "playwright":
        if Confirm.ask("Run `python -m playwright install chromium` now?", default=True):
            result = subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                cwd=str(ROOT),
                check=False,
            )
            if result.returncode != 0:
                console.print("[red]Playwright install failed. You can rerun setup.py or run the command manually.[/red]")
        env_values.pop("CHROME_EXECUTABLE", None)
        env_values.pop("CHROME_USER_DATA", None)
        return

    if choice == "existing":
        exe = Prompt.ask("Chrome executable path", default=env_values.get("CHROME_EXECUTABLE", ""))
        profile = Prompt.ask("Chrome user data directory", default=env_values.get("CHROME_USER_DATA", ""))
        if exe:
            env_values["CHROME_EXECUTABLE"] = exe
        if profile:
            env_values["CHROME_USER_DATA"] = profile


def main() -> None:
    console = Console()
    console.print(Panel("Kairos setup\n\nThis writes settings.json and .env for the local gateway.", title="Setup"))

    env_values = load_env(ENV_PATH)
    settings = configure_provider(console, env_values)
    configure_chrome(console, env_values)

    SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    write_env(ENV_PATH, env_values)

    console.print(Panel.fit(
        f"Saved {SETTINGS_PATH.name} and {ENV_PATH.name}\n\n"
        "Start the gateway:\n"
        "  python -m agent.gateway\n\n"
        "Start the CLI in another terminal:\n"
        "  python -m agent.cli",
        title="Done",
        border_style="green",
    ))


if __name__ == "__main__":
    main()
