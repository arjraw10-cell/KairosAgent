# Minimal Python Agent Framework

This project provides a small tool-calling agent scaffold with:

- `read_file`
- `write_file`
- `edit_file`
- `shell`
- `change_directory`
- `get_current_directory`
- Playwright browser tools:
  - `browser_navigate`
  - `browser_click`
  - `browser_type`
  - `browser_extract`
  - `browser_snapshot`
- Dynamic skill tools:
  - `register_skill`
  - `list_current_tools`
- Customization tools:
  - `update_memory`
  - `update_preferences`
  - `update_identity`
- Runtime tools:
  - `run_subagent`
  - `reload_tools`

It now routes messages through an `AgentGateway`, so the CLI is just one transport. Other transports such as Telegram can forward inbound text into the same gateway and reuse the same session/runtime behavior.

## Setup

```powershell
pip install -r requirements.txt
playwright install chromium
```

Set environment variable:

```powershell
$env:GEMINI_API_KEY="your-key"
```

Or use a `.env` file in the project root:

```env
GEMINI_API_KEY="your-key"
TAVILY_API_KEY="your-key"
```

Optional local `llama.cpp` server settings:

```env
LLAMA_CPP_BASE_URL="http://127.0.0.1:8080/v1"
LLAMA_CPP_MODEL="local-model"
LLAMA_CPP_API_KEY="not-needed"
```

Optional cache settings:

```env
GEMINI_EXPLICIT_CACHE="true"
GEMINI_CACHE_TTL="300s"
GEMINI_CACHE_MIN_CHARS="12000"
OPENAI_PROMPT_CACHE_RETENTION="24h"
```

`GEMINI_EXPLICIT_CACHE` reuses the stable prompt prefix with Gemini when the serialized cached portion is large enough. The CLI token summary now prints `cached=` so you can confirm whether cache hits are happening.

## Run

```powershell
python -m agent.cli --use-llama false --root .
```

Run the agent against a different workspace without exposing the agent repo itself:

```powershell
python -m agent.cli --workspace C:\path\to\project
```

Resume a saved chat session:

```powershell
python -m agent.cli --use-llama false --root . --session 2 --resume true
```

Local `llama.cpp` usage:

```powershell
python -m agent.cli --use-llama true --root .
```

Chats are saved as numbered JSON transcripts under `chats/`, for example `1.json`, `2.json`, `3.json`. Starting a fresh CLI chat without `--session` automatically picks the next available number. Use `--session N --resume true` to continue a specific saved chat. The CLI prints per-turn and session token totals after each response.
The agent tracks a current working directory. File and shell tools resolve relative paths from that directory, and the working directory may be moved between the configured workspace and the agent home.

## Gateway

`agent.gateway.AgentGateway` is the messaging boundary for the runtime.

- Transports send a `GatewayRequest` with a `GatewayAddress` plus message text.
- The gateway resolves or creates the session runtime, invokes the agent, and persists the transcript.
- The CLI now just reads stdin and forwards messages into the gateway with `platform="cli"`, using auto-numbered sessions when `--session` is omitted.

Minimal transport example:

```python
from pathlib import Path
from agent.gateway import AgentGateway, GatewayAddress, GatewayRequest

gateway = AgentGateway.from_args(
    workspace_dir=Path(".").resolve(),
    agent_home_dir=Path(".").resolve(),
    use_llama=False,
    max_steps=20,
)

response = gateway.handle(
    GatewayRequest(
        address=GatewayAddress(platform="telegram", session="chat-123"),
        text="Summarize this repo",
        resume=True,
    )
)

print(response.text)
gateway.close()
```

## Browser Usage Pattern

1. Call `browser_navigate`.
2. Use `browser_snapshot` when you need a quick list of interactive elements.
3. Interact via `browser_click` / `browser_type` using selectors.
4. Use `browser_extract` when you want page text instead of structure.
5. Re-run `browser_snapshot` after navigation or interaction if the page changed.

## Adding a Tool

Define a function and decorate it with `@tool(...)`, then register the module/function.

```python
from agent.tooling import tool, ToolContext

@tool(
    name="echo",
    description="Echo text back.",
    input_schema={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    },
)
def echo(context: ToolContext, text: str) -> dict:
    return {"text": text}
```

## Dynamic Skills

Recommended flow:

1. Call `change_directory` with `location="skills_root"`.
2. Use `write_file` / `edit_file` to create or update the skill folder and its files.
3. Call `register_skill` with the skill folder name to load or reload it in the current session.

Explainer skills need `skill.md`.

Executor skills typically include:

- `skill.md`
- `schema.json`
- `start.bat`
- supporting code files such as `run.py`
