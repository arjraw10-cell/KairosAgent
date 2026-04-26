# Kairos

Kairos is a local personal agent you run from your terminal. It can edit files, run shell commands, use a browser, remember preferences, and load custom skills. The cool thing about it is that it's very minimal, so you can actually edit it, and its self-improving, so if you wanna add something, you can just ask the agent to add it and it will. Note that it probably has a few mistakes cause I made it, so please tell me if you find any.

## Quick Start

Install Python dependencies:

```powershell
pip install -r requirements.txt
```

Run the first-time setup script:

```powershell
.\kairos_start.bat
```

This adds the Kairos folder to your user `PATH`, then opens the setup wizard.

Close and reopen PowerShell after the first setup so the new `PATH` is loaded.

Start the gateway:

```powershell
kairos gateway
```

Open another PowerShell window and start the chat UI:

```powershell
kairos cli
```

That is the normal way to use Kairos.

## Commands

```powershell
kairos gateway      # Start the local gateway
kairos cli          # Open the terminal chat UI
kairos configure    # Re-run setup
```

Inside the chat UI:

```text
/help              Show commands
/new               Start a fresh session
/resume            Choose a saved session
/mode              Choose personalized or unbiased mode
/model <name>      Change the active model
/session           Show token usage
/exit              Quit
```

## Setup Wizard

Run setup again any time with:

```powershell
kairos configure
```

The wizard asks for:

- Provider
- Model
- API key
- Base URL for custom OpenAI-compatible endpoints
- Browser setup

It writes:

- `settings.json` for provider/model/base URL
- `.env` for API keys and browser settings

## Providers

Supported providers:

```text
gemini
openai
anthropic
openai_compatible
llama_cpp
```

The setup wizard handles the config, but the files look like this.

`settings.json`:

```json
{
  "provider": "openai",
  "model": "gpt-4.1-mini",
  "base_url": null
}
```

`.env`:

```env
GEMINI_API_KEY="..."
OPENAI_API_KEY="..."
ANTHROPIC_API_KEY="..."
OPENAI_COMPATIBLE_API_KEY="..."
OPENAI_COMPATIBLE_BASE_URL="http://127.0.0.1:8000/v1"
LLAMA_CPP_BASE_URL="http://127.0.0.1:8080/v1"
LLAMA_CPP_API_KEY="not-needed"
```

## Browser

Browser tools use Playwright Chromium. The setup wizard can install it for you.

Manual install:

```powershell
python -m playwright install chromium
```

To use your own Chrome, set these in `.env`:

```env
CHROME_EXECUTABLE="C:\Path\To\chrome.exe"
CHROME_USER_DATA="C:\Path\To\Chrome\User Data"
```

## Sessions

Kairos saves chats automatically:

```text
chats/<session-name>/transcript.json
```

Resume the latest session:

```powershell
kairos cli --resume true
```

Resume a specific session:

```powershell
kairos cli --session 2026-04-25_18-30-00 --resume true
```

## Folders

```text
agent/            Kairos source code
chats/            Saved sessions
customization/    Memory, user preferences, identity
skills/           Custom skills
settings.json     Provider/model config
.env              Local secrets and browser settings
```

## Troubleshooting

If `kairos` is not recognized, close and reopen PowerShell. If it still fails, run:

```powershell
.\kairos_start.bat
```

If the CLI cannot connect, start the gateway first:

```powershell
kairos gateway
```

If browser tools fail:

```powershell
python -m playwright install chromium
```

If provider calls fail, check `.env`, `settings.json`, and restart the gateway.

## Notes

- `.env` is ignored by git.
- `chats/`, `skills/`, and `customization/` contents are ignored by git except for `.gitkeep`.
- `setup.py` is a setup wizard for this repo, not a Python packaging script.
