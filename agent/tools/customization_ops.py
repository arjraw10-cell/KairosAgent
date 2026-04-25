from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.bootstrap import ensure_customization_files
from agent.tooling import ToolContext, tool


def _customization_root(context: ToolContext) -> Path:
    return ensure_customization_files(context.agent_home_dir)


def _update_customization_file(context: ToolContext, filename: str, content: str) -> dict[str, Any]:
    root = _customization_root(context)
    file_path = root / filename
    
    # Security: Ensure we are only writing within the customization root
    if not str(file_path.resolve()).startswith(str(root)):
         return {"ok": False, "error": "Access denied: Path outside customization root."}
         
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return {"ok": True, "path": str(file_path)}


@tool(
    name="update_memory",
    description="Update the agent's long-term memory file (memory.md). Supports append and overwrite modes.",
    input_schema={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The content to add or set."},
            "mode": {"type": "string", "enum": ["overwrite", "append"], "description": "Whether to overwrite or append. Defaults to overwrite."}
        },
        "required": ["content"],
    },
)
def update_memory(context: ToolContext, content: str, mode: str = "overwrite") -> dict[str, Any]:
    root = _customization_root(context)
    file_path = root / "memory.md"
    
    if mode == "append" and file_path.exists():
        existing_content = file_path.read_text(encoding="utf-8")
        final_content = existing_content.strip() + "\n\n" + content.strip()
    else:
        final_content = content
        
    return _update_customization_file(context, "memory.md", final_content)


@tool(
    name="update_preferences",
    description="Update the user-preferences.md file. Use this to store stylistic choices, formatting rules, or tool usage preferences.",
    input_schema={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The complete new content for the preferences file."}
        },
        "required": ["content"],
    },
)
def update_preferences(context: ToolContext, content: str) -> dict[str, Any]:
    return _update_customization_file(context, "user-preferences.md", content)


@tool(
    name="update_identity",
    description="Update the agent's identity.md file. Use this to refine your persona, tone, or stated mission.",
    input_schema={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The complete new content for the identity file."}
        },
        "required": ["content"],
    },
)
def update_identity(context: ToolContext, content: str) -> dict[str, Any]:
    return _update_customization_file(context, "identity.md", content)
