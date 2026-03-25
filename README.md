# Minimal Python Agent Framework

This project provides a small tool-calling agent scaffold with:

- `read_file`
- `write_file`
- `edit_file` (`replace_block_with_context`)
- `run_powershell`
- Playwright browser tools:
  - `browser_navigate`
  - `browser_scan` (compact summary + interactable elements)
  - `browser_click`
  - `browser_type`
  - `browser_wait_for`
  - `browser_extract`
  - `browser_snapshot` (on-demand deep detail)
  - `browser_get_snapshot`
  - `browser_history`
- Dynamic skill tools:
  - `create_skill_tool`
  - `list_current_tools`

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
python -m agent.cli --use-llama false --root . --session main --resume true
```

Local `llama.cpp` usage:

```powershell
python -m agent.cli --use-llama true --root .
```

Chats are saved under `chats/SESSION_NAME.json`. The CLI prints per-turn and session token totals after each response.
Skills and chats stay under the agent repo, while file and shell tools operate inside the configured workspace.

## Browser Usage Pattern

1. Call `browser_navigate`.
2. Call `browser_scan` first (small context footprint).
3. Interact via `browser_click` / `browser_type` using returned `element_id`.
4. Use `browser_wait_for` before actions on dynamic pages.
5. Use `browser_extract` for targeted content pull instead of full-page snapshot.
6. Only call `browser_snapshot` if exact page structure is required.
7. Use `browser_history` to inspect recent actions and avoid loops.

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

## Dynamic Skill Creator

Use `create_skill_tool` to:
1. Write skill code to a Python file path you choose.
2. Write tool metadata/schema to a sibling `.schema.json` file.
3. Register the new tool in the current running agent session.

The code should define a callable function (default name: `run`) with signature:

```python
def run(context, **kwargs) -> dict:
    ...
```
