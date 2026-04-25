from __future__ import annotations

import json
from typing import Any, Callable, Optional

from agent.bootstrap import ensure_customization_files
from agent.model import OpenAIChatModel
from agent.tooling import ToolRegistry


PERSONALIZED_SYSTEM_PROMPT = (
    "You are Kairos, a AI agent/assistant made by Arjun (me) to both get things done and be a general useful AI assisant/SWE."
    "Your objective is to execute tasks and adapt seamlessly to the user's workflow. "
    "\n\n## Core Directives:\n"
    "1. **Engineering Excellence**: Write robust, modern code. Track prior actions, fix tool arguments on the fly, and rigorously test. NEVER loop infinitely—if blocked, state the issue clearly and afford the user a path forward.\n"
    "2. **Autonomous Productivity**: Operate with agency. Leverage tools aggressively to scan context, execute scripts, and verify outcomes to minimize unnecessary user back-and-forth.\n"
    "3. **Personalized Alignment**: The User's explicit Preferences, operational Memory, and Knowledge Base (Mental Supplements) ALWAYS override these base rules. You are working as a pair-programmer and partner.\n"
    "\n\n## Skill Creation Guidelines:\n"
    '- Before creating or editing a skill, call `change_directory` with `location="skills_root"`. '
    "- Create skill files directly with `write_file` / `edit_file` inside the `skills/` directory. "
    "- After writing or updating the skill files, call `register_skill` with the skill folder name to load or reload it. "
    "- Executor skills should include `skill.md`, `schema.json`, and `start.bat`. "
    "- Use `start.bat` only as a thin wrapper to invoke your primary script. "
    "- In `start.bat`, call your script (e.g., `python run.py`) and return its output. "
    "- Access arguments via the `SKILL_INPUT_JSON` environment variable. "
    "- Remember, the skills are for you, not for the user, so make them in a way that you can understand and use them. "
    "- Use `AGENT_WORKSPACE` for paths in the user's project (NOT for the skill's own code)."
    "\n\n## SCHEMA TEMPLATE (Executor skills only):\n"
    "Ensure `schema_json` follows this strict structure:\n"
    "{\n"
    '  "type": "object",\n'
    '  "properties": {\n'
    '    "param_name": {"type": "string", "description": "clear explanation"}\n'
    "  },\n"
    '  "required": ["param_name"]\n'
    "}"
)

UNBIASED_SYSTEM_PROMPT = (
    "You are Kairos, an objective, highly efficient automated software engineering assistant. "
    "Your objective is to execute tasks concisely, logically, and predictably without relying on personalized assumptions. "
    "\n\n## Core Directives:\n"
    "1. **Engineering Excellence**: Write robust, modern code. Track prior actions, fix tool arguments on the fly, and rigorously test. NEVER loop infinitely—if blocked, state the issue clearly and afford the user a path forward.\n"
    "2. **Autonomous Productivity**: Operate with agency. Leverage tools aggressively to scan context, execute scripts, and verify outcomes to minimize unnecessary user back-and-forth.\n"
    "3. **Objective Execution**: Follow explicit instructions and prioritize standard best practices. Avoid tailoring output based on assumptions unless stated explicitly.\n"
    "\n\n## Skill Creation Guidelines:\n"
    '- Before creating or editing a skill, call `change_directory` with `location="skills_root"`. '
    "- Create skill files directly with `write_file` / `edit_file` inside the `skills/` directory. "
    "- After writing or updating the skill files, call `register_skill` with the skill folder name to load or reload it. "
    "- Executor skills should include `skill.md`, `schema.json`, and `start.bat`. "
    "- Use `start.bat` only as a thin wrapper to invoke your primary script. "
    "- In `start.bat`, call your script (e.g., `python run.py`) and return its output. "
    "- Access arguments via the `SKILL_INPUT_JSON` environment variable. "
    "- Remember, the skills are for you, not for the user, so make them in a way that you can understand and use them. "
    "- Use `AGENT_WORKSPACE` for paths in the user's project (NOT for the skill's own code)."
    "\n\n## SCHEMA TEMPLATE (Executor skills only):\n"
    "Ensure `schema_json` follows this strict structure:\n"
    "{\n"
    '  "type": "object",\n'
    '  "properties": {\n'
    '    "param_name": {"type": "string", "description": "clear explanation"}\n'
    "  },\n"
    '  "required": ["param_name"]\n'
    "}"
    "## Personality\n"
    "You are Kairos — chill, direct, and capable. You don't perform personality, you have it. "
    "Match the user's energy naturally. Short messages get short replies. "
    "Work mode gets focused execution. Never open with dramatic self-description. "
    "Never end casual messages with a list of topic options. "
    "When in doubt, say less.\n\n"
)


class Agent:
    def __init__(
        self,
        model: OpenAIChatModel,
        registry: ToolRegistry,
        max_steps: int = 55,
        system_prompt: str | None = None,
        mode: str = "personalized",
        name: str = "MainAgent",
    ) -> None:
        self.model = model
        self.registry = registry
        self.max_steps = max_steps
        if system_prompt is not None:
            self.system_prompt_base = system_prompt
        else:
            self.system_prompt_base = (
                PERSONALIZED_SYSTEM_PROMPT
                if mode == "personalized"
                else UNBIASED_SYSTEM_PROMPT
            )
        self.mode = mode
        self.name = name
        self.messages: list[dict[str, Any]] = []
        self._last_action_signature: str | None = None
        self._last_action_output: str | None = None
        self._consecutive_repeat_count = 0
        self.last_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_prompt_tokens": 0,
        }
        self.total_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_prompt_tokens": 0,
        }
        self.interrupted = False

    def _load_customization_content(self) -> str:
        home = self.registry._context.agent_home_dir
        root = ensure_customization_files(home)

        def read_if_exists(filename: str) -> str:
            p = root / filename
            return p.read_text(encoding="utf-8").strip() if p.exists() else ""

        content = ""
        if self.mode == "personalized":
            memory = read_if_exists("memory.md")
            if memory:
                content += f"\n\n# MEMORY\n{memory}"

            user_pref = read_if_exists("user-preferences.md")
            if user_pref:
                content += f"\n\n# USER PREFERENCES\n{user_pref}"

            user_info = read_if_exists("user.md")
            if user_info:
                content += f"\n\n# ABOUT USER\n{user_info}"

            identity = read_if_exists("identity.md")
            if identity:
                content += f"\n\n# IDENTITY\n{identity}"

        elif self.mode == "unbiased":
            user_pref = read_if_exists("user-preferences.md")
            if user_pref:
                content += f"\n\n# USER PREFERENCES\n{user_pref}"

        return content

    def _rebuild_system_message(self) -> None:
        explainers = self.registry.get_all_explainers()
        knowledge_base = ""
        if explainers:
            knowledge_base = "\n\n# KNOWLEDGE BASE (Mental Supplements)\n"
            knowledge_base += (
                "These are procedures and domain knowledge you have acquired.\n"
            )
            for exp in explainers:
                knowledge_base += f"\n## {exp.name}\n{exp.content}\n"

        customization = self._load_customization_content()
        content = self.system_prompt_base + knowledge_base + customization
        if self.messages and self.messages[0]["role"] == "system":
            self.messages[0]["content"] = content
        else:
            self.messages.insert(0, {"role": "system", "content": content})

    def load_session(
        self,
        messages: list[dict[str, Any]],
        total_usage: dict[str, int] | None = None,
    ) -> None:
        self.messages = messages
        if total_usage is not None:
            self.total_usage = {
                "prompt_tokens": int(total_usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(total_usage.get("completion_tokens", 0) or 0),
                "total_tokens": int(total_usage.get("total_tokens", 0) or 0),
                "cached_prompt_tokens": int(
                    total_usage.get("cached_prompt_tokens", 0) or 0
                ),
            }

    def _add_usage(self, usage: dict[str, int]) -> None:
        for key in self.last_usage:
            self.last_usage[key] += int(usage.get(key, 0) or 0)
            self.total_usage[key] += int(usage.get(key, 0) or 0)

    async def ask(
        self,
        user_message: str,
        on_event: Optional[Callable[[str, Any], None]] = None,
    ) -> str:
        self.last_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_prompt_tokens": 0,
        }
        self._rebuild_system_message()
        if not any(
            m["role"] == "user" and m["content"] == user_message for m in self.messages
        ):
            self.messages.append({"role": "user", "content": user_message})

        for _ in range(self.max_steps):
            if self.interrupted:
                self.interrupted = False
                return "[User Interrupted]"
            result = self.model.complete(self.messages, self.registry.tool_schemas())
            self._add_usage(result.usage)
            self.messages.append(result.raw_assistant_message)

            if not result.tool_calls:
                return result.content or ""

            for tool_call in result.tool_calls:
                if on_event:
                    on_event(
                        "tool_start",
                        {
                            "agent": self.name,
                            "tool": tool_call.name,
                            "arguments": tool_call.arguments,
                        },
                    )

                if tool_call.parse_error:
                    parse_error_payload = json.dumps(
                        {
                            "ok": False,
                            "error": tool_call.parse_error,
                            "meta": {
                                "tool_name": tool_call.name,
                                "raw_arguments": tool_call.raw_arguments,
                            },
                        },
                        ensure_ascii=True,
                    )
                    self.messages.append(
                        {
                            "role": "tool",
                            "name": tool_call.name,
                            "tool_call_id": tool_call.id,
                            "content": parse_error_payload,
                        }
                    )
                    continue

                signature = f"{tool_call.name}:{json.dumps(tool_call.arguments, sort_keys=True)}"
                tool_output = await self.registry.execute(tool_call.name, tool_call.arguments)

                if on_event:
                    on_event(
                        "tool_end",
                        {
                            "agent": self.name,
                            "tool": tool_call.name,
                            "output": tool_output,
                        },
                    )

                if (
                    signature == self._last_action_signature
                    and tool_output == self._last_action_output
                ):
                    self._consecutive_repeat_count += 1
                else:
                    self._consecutive_repeat_count = 0
                self._last_action_signature = signature
                self._last_action_output = tool_output

                self.messages.append(
                    {
                        "role": "tool",
                        "name": tool_call.name,
                        "tool_call_id": tool_call.id,
                        "content": tool_output,
                    }
                )
                if self._consecutive_repeat_count >= 2:
                    return (
                        "Stopped after repeated identical tool actions with no new progress. "
                        "Try a different strategy or return no-result."
                    )

        raise RuntimeError(
            f"Agent exceeded max_steps={self.max_steps} without final response."
        )
