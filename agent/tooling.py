from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agent.security import resolve_path_from_base


@dataclass
class ToolContext:
    root_dir: Path
    agent_home_dir: Path
    runtime_state: dict[str, Any]

    def allowed_roots(self) -> list[Path]:
        roots = [self.root_dir.resolve(), self.agent_home_dir.resolve()]
        unique_roots: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            root_key = str(root)
            if root_key in seen:
                continue
            seen.add(root_key)
            unique_roots.append(root)
        return unique_roots

    def current_dir(self) -> Path:
        current = self.runtime_state.get("cwd")
        if current is None:
            current = self.root_dir.resolve()
            self.runtime_state["cwd"] = str(current)
            return current
        resolved = Path(str(current)).resolve()
        if resolved != self.root_dir.resolve():
            resolve_path_from_base(
                base_dir=resolved,
                user_path=".",
                allowed_roots=self.allowed_roots(),
            )
        return resolved

    def set_current_dir(self, path: Path) -> Path:
        resolved = resolve_path_from_base(
            base_dir=self.current_dir(),
            user_path=str(path),
            allowed_roots=self.allowed_roots(),
        )
        if not resolved.exists():
            raise FileNotFoundError(f"Directory not found: {resolved}")
        if not resolved.is_dir():
            raise NotADirectoryError(f"Path is not a directory: {resolved}")
        self.runtime_state["cwd"] = str(resolved)
        return resolved

    def resolve_path(self, user_path: str) -> Path:
        return resolve_path_from_base(
            base_dir=self.current_dir(),
            user_path=user_path,
            allowed_roots=self.allowed_roots(),
        )


@dataclass
class ExplainerSpec:
    name: str
    content: str


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
        self._explainers: dict[str, ExplainerSpec] = {}

    def register(self, func: Callable[..., dict[str, Any]], origin: str = "base") -> None:
        spec = getattr(func, "__tool_spec__", None)
        if spec is None:
            raise ValueError(f"Function {func.__name__} is missing @tool metadata.")
        if spec.name in self._tools:
            # Allow overwriting dynamic tools
            if spec.origin == "base" and self._tools[spec.name].origin == "base":
                raise ValueError(f"Tool already registered: {spec.name}")
        spec.origin = origin
        self._tools[spec.name] = spec

    def register_explainer(self, name: str, content: str) -> None:
        self._explainers[name] = ExplainerSpec(name=name, content=content)

    def register_module(self, module: Any, origin: str = "base") -> None:
        for _, value in inspect.getmembers(module):
            if callable(value) and hasattr(value, "__tool_spec__"):
                self.register(value, origin=origin)

    def tool_schemas(self) -> list[dict[str, Any]]:
        return [t.openai_schema() for t in self._tools.values()]

    def list_tools(self) -> list[dict[str, str]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "origin": t.origin,
            }
            for t in self._tools.values()
        ]

    def list_explainers(self) -> list[dict[str, str]]:
        return [{"name": e.name} for e in self._explainers.values()]

    def get_all_explainers(self) -> list[ExplainerSpec]:
        return list(self._explainers.values())

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        spec = self._tools.get(tool_name)
        if spec is None:
            payload = {"ok": False, "error": f"Unknown tool: {tool_name}"}
            return json.dumps(payload)

        try:
            if inspect.iscoroutinefunction(spec.func):
                data = await spec.func(self._context, **arguments)
            else:
                data = spec.func(self._context, **arguments)
            payload = {"ok": True, "data": data}
        except Exception as exc:  # noqa: BLE001
            payload = {"ok": False, "error": str(exc)}
        return json.dumps(payload, ensure_ascii=True)
