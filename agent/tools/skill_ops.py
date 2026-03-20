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


def _resolve_skill_path(context: ToolContext, file_path: str) -> Path:
    candidate = Path(file_path)
    if candidate.is_absolute():
        return resolve_path(context.agent_home_dir, str(candidate))
    return resolve_path(context.agent_home_dir, file_path)


def _build_dynamic_tool(
    tool_name: str,
    description: str,
    parameters_schema: dict[str, Any],
    raw_func: Any,
) -> Any:
    @tool(name=tool_name, description=description, input_schema=parameters_schema)
    def dynamic_tool(ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        result = raw_func(ctx, **kwargs)
        if not isinstance(result, dict):
            raise TypeError(
                f"Dynamic skill '{tool_name}' must return a dict. Got: {type(result).__name__}"
            )
        return result

    return dynamic_tool


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
    abs_file = _resolve_skill_path(context, file_path)
    if abs_file.suffix.lower() != ".py":
        raise ValueError("file_path must end with .py")

    abs_schema = abs_file.with_suffix(".schema.json")
    if (abs_file.exists() or abs_schema.exists()) and not overwrite:
        raise FileExistsError(
            f"Skill files already exist. Use overwrite=true to replace: {file_path}"
        )

    skill_meta = {
        "tool_name": tool_name,
        "description": description,
        "function_name": function_name,
        "parameters_schema": parameters_schema,
        "source_file": str(abs_file),
    }

    abs_file.parent.mkdir(parents=True, exist_ok=True)
    previous_code = abs_file.read_text(encoding="utf-8") if abs_file.exists() else None
    previous_schema = abs_schema.read_text(encoding="utf-8") if abs_schema.exists() else None
    code_written = False
    schema_written = False
    try:
        abs_file.write_text(code, encoding="utf-8")
        code_written = True

        module = _load_module_from_path(abs_file)
        raw_func = getattr(module, function_name, None)
        if raw_func is None or not callable(raw_func):
            raise ValueError(
                f"Function '{function_name}' not found or not callable in {file_path}"
            )

        dynamic_tool = _build_dynamic_tool(tool_name, description, parameters_schema, raw_func)
        registry.register(dynamic_tool, origin="agent_made_skill")

        abs_schema.write_text(json.dumps(skill_meta, indent=2, ensure_ascii=True), encoding="utf-8")
        schema_written = True
    except Exception:
        if code_written:
            if previous_code is None:
                if abs_file.exists():
                    abs_file.unlink()
            else:
                abs_file.write_text(previous_code, encoding="utf-8")
        if schema_written:
            if previous_schema is None:
                if abs_schema.exists():
                    abs_schema.unlink()
            else:
                abs_schema.write_text(previous_schema, encoding="utf-8")
        raise

    return {
        "registered": True,
        "tool_name": tool_name,
        "function_name": function_name,
        "source_path": str(abs_file),
        "schema_path": str(abs_schema),
    }


def load_skills(registry: Any, skills_dir: Path) -> None:
    """Scan directory for .schema.json files and register them as tools."""
    if not skills_dir.exists() or not skills_dir.is_dir():
        return

    for schema_file in skills_dir.glob("*.schema.json"):
        try:
            meta = json.loads(schema_file.read_text(encoding="utf-8"))
            tool_name = meta["tool_name"]
            description = meta["description"]
            function_name = meta.get("function_name", "run")
            parameters_schema = meta["parameters_schema"]
            source_file = Path(meta["source_file"])

            # Fallback if the path in meta is no longer valid
            if not source_file.is_absolute():
                source_file = schema_file.parent / source_file.name
            if not source_file.exists():
                source_file = schema_file.with_name(schema_file.name.replace(".schema.json", ".py"))

            if not source_file.exists():
                print(f"Warning: Source file for skill '{tool_name}' not found at {source_file}")
                continue

            module = _load_module_from_path(source_file)
            raw_func = getattr(module, function_name, None)
            if raw_func is None or not callable(raw_func):
                print(f"Warning: Function '{function_name}' not found in {source_file}")
                continue

            dynamic_tool = _build_dynamic_tool(tool_name, description, parameters_schema, raw_func)
            registry.register(dynamic_tool, origin="agent_made_skill")
        except Exception as exc:
            print(f"Error loading skill from {schema_file}: {exc}")


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
    tools = registry.list_tools()
    base_tools = sorted([item["name"] for item in tools if item["origin"] == "base"])
    agent_made_skills = sorted(
        [item["name"] for item in tools if item["origin"] == "agent_made_skill"]
    )
    return {
        "count": len(tools),
        "base_tools": base_tools,
        "agent_made_skills": agent_made_skills,
    }
