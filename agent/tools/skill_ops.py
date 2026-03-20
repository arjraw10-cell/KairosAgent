from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path
from typing import Any

from agent.security import resolve_path
from agent.tooling import ToolContext, tool


def _require_registry(context: ToolContext) -> Any:
    registry = context.runtime_state.get("registry")
    if registry is None:
        raise RuntimeError("Tool registry is not available in runtime_state.")
    return registry


def _load_module_from_path(file_path: Path) -> Any:
    module_name = f"dynamic_skill_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from: {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tool(
    name="create_skill_tool",
    description=(
        "Create a new tool skill by writing a Python source file and JSON schema file, "
        "then register it into the current agent toolset."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "description": {"type": "string"},
            "file_path": {"type": "string"},
            "function_name": {"type": "string", "default": "run"},
            "code": {"type": "string"},
            "parameters_schema": {"type": "object"},
            "overwrite": {"type": "boolean", "default": False},
        },
        "required": ["tool_name", "description", "file_path", "code", "parameters_schema"],
        "additionalProperties": False,
    },
)
def create_skill_tool(
    context: ToolContext,
    tool_name: str,
    description: str,
    file_path: str,
    code: str,
    parameters_schema: dict[str, Any],
    function_name: str = "run",
    overwrite: bool = False,
) -> dict[str, Any]:
    registry = _require_registry(context)
    abs_file = resolve_path(context.root_dir, file_path)
    if abs_file.suffix.lower() != ".py":
        raise ValueError("file_path must end with .py")

    abs_schema = abs_file.with_suffix(".schema.json")
    if (abs_file.exists() or abs_schema.exists()) and not overwrite:
        raise FileExistsError(
            f"Skill files already exist. Use overwrite=true to replace: {file_path}"
        )

    abs_file.parent.mkdir(parents=True, exist_ok=True)
    abs_file.write_text(code, encoding="utf-8")

    skill_meta = {
        "tool_name": tool_name,
        "description": description,
        "function_name": function_name,
        "parameters_schema": parameters_schema,
        "source_file": str(abs_file),
    }
    abs_schema.write_text(json.dumps(skill_meta, indent=2, ensure_ascii=True), encoding="utf-8")

    module = _load_module_from_path(abs_file)
    raw_func = getattr(module, function_name, None)
    if raw_func is None or not callable(raw_func):
        raise ValueError(
            f"Function '{function_name}' not found or not callable in {file_path}"
        )

    # Wrap user function into this agent's tool contract and attach schema metadata.
    @tool(name=tool_name, description=description, input_schema=parameters_schema)
    def dynamic_tool(ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        result = raw_func(ctx, **kwargs)
        if not isinstance(result, dict):
            raise TypeError(
                f"Dynamic skill '{tool_name}' must return a dict. Got: {type(result).__name__}"
            )
        return result

    registry.register(dynamic_tool)
    return {
        "registered": True,
        "tool_name": tool_name,
        "function_name": function_name,
        "source_path": str(abs_file),
        "schema_path": str(abs_schema),
    }


@tool(
    name="list_current_tools",
    description="List the currently registered tool names available to the agent.",
    input_schema={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
)
def list_current_tools(context: ToolContext) -> dict[str, Any]:
    registry = _require_registry(context)
    tools = registry.tool_schemas()
    names = [item.get("function", {}).get("name", "") for item in tools]
    return {"count": len(names), "tools": sorted([n for n in names if n])}
