from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from agent.tooling import ToolContext, tool, ToolRegistry

def _skills_root(context: ToolContext) -> Path:
    return (context.agent_home_dir / "skills").resolve()

def _require_registry(context: ToolContext) -> ToolRegistry:
    registry = context.runtime_state.get("registry")
    if registry is None:
        raise RuntimeError("Tool registry is not available in runtime_state.")
    return registry

def _normalize_schema(node: Any) -> Any:
    """Force properties to be dictionaries and add missing required fields."""
    if not isinstance(node, dict):
        return node
    
    # If this is a properties container, normalize its children
    if "properties" in node and isinstance(node["properties"], dict):
        new_props = {}
        for k, v in node["properties"].items():
            if isinstance(v, str):
                # Convert "prop": "description" -> "prop": {"type": "string", "description": "description"}
                new_props[k] = {"type": "string", "description": v}
            elif isinstance(v, dict):
                new_props[k] = _normalize_schema(v)
            else:
                new_props[k] = v
        node["properties"] = new_props

    # Ensure type is present for objects and properties
    if "properties" in node and "type" not in node:
        node["type"] = "object"
    
    # Recursive for nested objects
    for k, v in node.items():
        if isinstance(v, dict) and k != "properties":
            node[k] = _normalize_schema(v)
            
    return node

def _clean_schema(node: Any) -> Any:
    """Recursively ensure that no parts of the schema are stringified JSON and normalize structure."""
    if isinstance(node, str) and node.strip().startswith(("{", "[")):
        try:
            parsed = json.loads(node)
            if isinstance(parsed, (dict, list)):
                return _clean_schema(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
    
    if isinstance(node, dict):
        cleaned = {k: _clean_schema(v) for k, v in node.items()}
        return _normalize_schema(cleaned)
    
    if isinstance(node, list):
        return [_clean_schema(i) for i in node]
    
    return node

def _run_executor(context: ToolContext, skill_dir: Path, **kwargs: Any) -> dict[str, Any]:
    # Set up environment variables
    env = os.environ.copy()
    env["AGENT_WORKSPACE"] = str(context.root_dir)
    env["AGENT_SKILL_DIR"] = str(skill_dir)
    env["AGENT_HOME_DIR"] = str(context.agent_home_dir)
    
    # Start.bat should be in skill_dir
    bat_path = skill_dir / "start.bat"
    if not bat_path.exists():
        raise FileNotFoundError(f"Executor skill missing start.bat in {skill_dir}")

    # Prepare input for the script via env var
    env["SKILL_INPUT_JSON"] = json.dumps(kwargs)

    try:
        result = subprocess.run(
            [str(bat_path)],
            capture_output=True,
            text=True,
            cwd=str(skill_dir),
            env=env,
            shell=True,
            check=False,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "ok": result.returncode == 0
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

def _build_executor_tool(tool_name: str, description: str, parameters_schema: dict[str, Any], skill_dir: Path) -> Any:
    @tool(name=tool_name, description=description, input_schema=parameters_schema)
    def executor_tool(ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        return _run_executor(ctx, skill_dir, **kwargs)
    return executor_tool

def _load_skill_path(registry: ToolRegistry, skill_path: Path) -> dict[str, Any]:
    if not skill_path.is_dir():
        raise FileNotFoundError(f"Skill directory not found: {skill_path}")
    if skill_path.name.startswith("__"):
        raise ValueError(f"Reserved skill directory name: {skill_path.name}")

    skill_md_path = skill_path / "skill.md"
    if not skill_md_path.exists():
        raise FileNotFoundError(f"Skill is missing skill.md: {skill_path}")

    skill_md = skill_md_path.read_text(encoding="utf-8")
    schema_path = skill_path / "schema.json"

    if schema_path.exists():
        schema = _clean_schema(json.loads(schema_path.read_text(encoding="utf-8")))
        tool_name = schema.get("name") or skill_path.name
        params = (
            schema.get("parameters", schema)
            if any(key in schema for key in ["parameters", "type"])
            else schema
        )
        desc = schema.get("description", tool_name)
        tool_fn = _build_executor_tool(tool_name, desc, params, skill_path)
        registry.register(tool_fn, origin="agent_made_skill")
        return {
            "name": skill_path.name,
            "type": "Executor",
            "tool_name": tool_name,
            "path": str(skill_path),
        }

    registry.register_explainer(skill_path.name, skill_md)
    return {
        "name": skill_path.name,
        "type": "Explainer",
        "path": str(skill_path),
    }


@tool(
    name="register_skill",
    description=(
        "Register or reload a skill that already exists under the skills directory. "
        "Typical flow: change_directory to skills_root, write the skill files, then call register_skill."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Skill folder name under the skills directory.",
            },
        },
        "required": ["name"],
        "additionalProperties": False,
    },
)
def register_skill(context: ToolContext, name: str) -> dict[str, Any]:
    registry = _require_registry(context)
    skill_dir = _skills_root(context) / name
    result = _load_skill_path(registry, skill_dir)
    return {"status": "registered", **result}

def load_skills(registry: ToolRegistry, skills_dir: Path) -> None:
    if not skills_dir.exists() or not skills_dir.is_dir():
        return

    for skill_path in skills_dir.iterdir():
        if not skill_path.is_dir() or skill_path.name.startswith("__"):
            continue
        try:
            _load_skill_path(registry, skill_path)
        except FileNotFoundError:
            continue
        except Exception as exc:
            print(f"Error loading skill {skill_path.name}: {exc}")

@tool(
    name="list_current_tools",
    description="List all registered tools and knowledge explainers (Mental Supplements).",
    input_schema={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
)
def list_current_tools(context: ToolContext) -> dict[str, Any]:
    registry = _require_registry(context)
    tools = registry.list_tools()
    explainers = registry.list_explainers()
    
    base_tools = sorted([item["name"] for item in tools if item["origin"] == "base"])
    agent_made_executors = sorted([item["name"] for item in tools if item["origin"] == "agent_made_skill"])
    explainer_names = sorted([item["name"] for item in explainers])
    
    return {
        "tool_count": len(tools),
        "explainer_count": len(explainers),
        "base_tools": base_tools,
        "executor_skills": agent_made_executors,
        "explainer_skills": explainer_names,
    }
