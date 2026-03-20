from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class ToolContext:
    root_dir: Path
    agent_home_dir: Path
    runtime_state: dict[str, Any]


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    func: Callable[..., dict[str, Any]]
    origin: str = "base"

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


def tool(name: str, description: str, input_schema: dict[str, Any]) -> Callable[[Callable[..., dict[str, Any]]], Callable[..., dict[str, Any]]]:
    def decorator(func: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
        setattr(
            func,
            "__tool_spec__",
            ToolSpec(name=name, description=description, input_schema=input_schema, func=func),
        )
        return func

    return decorator


class ToolRegistry:
    def __init__(self, context: ToolContext) -> None:
        self._context = context
        self._tools: dict[str, ToolSpec] = {}

    def register(self, func: Callable[..., dict[str, Any]], origin: str = "base") -> None:
        spec = getattr(func, "__tool_spec__", None)
        if spec is None:
            raise ValueError(f"Function {func.__name__} is missing @tool metadata.")
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        spec.origin = origin
        self._tools[spec.name] = spec

    def register_module(self, module: Any, origin: str = "base") -> None:
        for _, value in inspect.getmembers(module):
            if callable(value) and hasattr(value, "__tool_spec__"):
                self.register(value, origin=origin)

    def tool_schemas(self) -> list[dict[str, Any]]:
        return [tool.openai_schema() for tool in self._tools.values()]

    def list_tools(self) -> list[dict[str, str]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "origin": tool.origin,
            }
            for tool in self._tools.values()
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        spec = self._tools.get(tool_name)
        if spec is None:
            payload = {"ok": False, "error": f"Unknown tool: {tool_name}"}
            return json.dumps(payload)

        try:
            data = spec.func(self._context, **arguments)
            payload = {"ok": True, "data": data}
        except Exception as exc:  # noqa: BLE001
            payload = {"ok": False, "error": str(exc)}
        return json.dumps(payload, ensure_ascii=True)
