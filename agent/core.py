from __future__ import annotations

import json
from typing import Any

from agent.model import OpenAIChatModel
from agent.tooling import ToolRegistry


DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful coding agent. Use tools when needed. "
    "Start browser tasks with browser_scan for compact context and call browser_snapshot only if needed. "
    "Track prior actions and avoid repeating the same no-progress action. "
    "If a tool reports argument/JSON errors, correct the tool call and retry. "
    "It is acceptable to end with no results when progress is blocked."
)


class Agent:
    def __init__(
        self,
        model: OpenAIChatModel,
        registry: ToolRegistry,
        max_steps: int = 20,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        self.model = model
        self.registry = registry
        self.max_steps = max_steps
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
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
                "cached_prompt_tokens": int(total_usage.get("cached_prompt_tokens", 0) or 0),
            }

    def _add_usage(self, usage: dict[str, int]) -> None:
        for key in self.last_usage:
            self.last_usage[key] += int(usage.get(key, 0) or 0)
            self.total_usage[key] += int(usage.get(key, 0) or 0)

    def ask(self, user_message: str) -> str:
        self.last_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_prompt_tokens": 0,
        }
        self.messages.append({"role": "user", "content": user_message})
        for _ in range(self.max_steps):
            result = self.model.complete(self.messages, self.registry.tool_schemas())
            self._add_usage(result.usage)
            self.messages.append(result.raw_assistant_message)

            if not result.tool_calls:
                return result.content or ""

            for tool_call in result.tool_calls:
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
                tool_output = self.registry.execute(tool_call.name, tool_call.arguments)
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

        raise RuntimeError(f"Agent exceeded max_steps={self.max_steps} without final response.")
