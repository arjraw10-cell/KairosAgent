from __future__ import annotations

from typing import Any

from agent.tooling import ToolContext, tool


@tool(
    name="change_directory",
    description=(
        "Change the agent working directory. "
        "Use location='skills_root' before creating or editing skills. "
        "Relative paths are resolved from the current working directory."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative or absolute directory path."},
            "location": {
                "type": "string",
                "enum": ["workspace_root", "agent_home", "skills_root"],
                "description": "Convenience location for common directories.",
            },
        },
        "additionalProperties": False,
    },
)
def change_directory(
    context: ToolContext,
    path: str | None = None,
    location: str | None = None,
) -> dict[str, Any]:
    provided = [path is not None, location is not None]
    if sum(1 for item in provided if item) != 1:
        raise ValueError("Provide exactly one of path or location.")

    if location == "workspace_root":
        resolved = context.set_current_dir(context.root_dir)
    elif location == "agent_home":
        resolved = context.set_current_dir(context.agent_home_dir)
    elif location == "skills_root":
        skills_root = (context.agent_home_dir / "skills").resolve()
        skills_root.mkdir(parents=True, exist_ok=True)
        resolved = context.set_current_dir(skills_root)
    else:
        resolved = context.set_current_dir(context.resolve_path(path or "."))

    return {
        "current_dir": str(resolved),
        "workspace_root": str(context.root_dir.resolve()),
        "agent_home_dir": str(context.agent_home_dir.resolve()),
    }


@tool(
    name="get_current_directory",
    description="Return the current working directory and allowed root directories.",
    input_schema={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
)
def get_current_directory(context: ToolContext) -> dict[str, Any]:
    return {
        "current_dir": str(context.current_dir()),
        "workspace_root": str(context.root_dir.resolve()),
        "agent_home_dir": str(context.agent_home_dir.resolve()),
        "allowed_roots": [str(root) for root in context.allowed_roots()],
    }
