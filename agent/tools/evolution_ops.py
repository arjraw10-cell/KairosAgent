import importlib
import pkgutil
import sys
from agent.tooling import tool, ToolContext
from agent.tools.skill_ops import load_skills
from typing import Any

@tool(
    name="reload_tools",
    description="Reload all tools and skills into the registry to reflect recent code changes or new tool files.",
    input_schema={
        "type": "object",
        "properties": {},
        "required": []
    }
)
def reload_tools(context: ToolContext) -> dict[str, Any]:
    """
    Forces the registry to clear and re-load built-in and custom tools/skills by deep-reloading the tools package.
    """
    registry = context.runtime_state.get("registry")
    if not registry:
        return {"ok": False, "error": "No registry found in context."}
    
    # Clear registry tools
    registry._tools.clear()
    registry._explainers.clear()
    
    # Reload the tools package and all its submodules
    import agent.tools as tools_pkg
    
    # Find all submodules
    submodules = []
    for loader, module_name, is_pkg in pkgutil.walk_packages(tools_pkg.__path__, tools_pkg.__name__ + "."):
        submodules.append(module_name)
    
    # Reload submodules
    for mod_name in submodules:
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])
        else:
            try:
                importlib.import_module(mod_name)
            except Exception:
                continue # Skip modules that can't be imported
                
    # Reload the main tools package
    importlib.reload(tools_pkg)
    
    # Re-register tools from all submodules
    for mod_name in submodules:
        if mod_name in sys.modules:
            registry.register_module(sys.modules[mod_name], origin="base")
    
    # Reload skills
    load_skills(registry, context.agent_home_dir / "skills")
    
    return {
        "ok": True, 
        "message": f"Successfully reloaded {len(submodules)} tool modules and all skills.",
        "modules": [m.split(".")[-1] for m in submodules]
    }
