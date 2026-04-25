from __future__ import annotations

import uuid
from typing import Optional

from agent.tooling import tool, ToolContext


@tool(
    name="run_subagent",
    description="Run a subagent to perform a specific task. Useful for delegating work to an autonomous instance.",
    input_schema={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "What the subagent should do."
            },
            "personalized": {
                "type": "boolean",
                "description": "Whether the subagent runs in personalized mode. Defaults to false."
            },
            "session_id": {
                "type": "string",
                "description": "Optional session ID to resume a previous conversation."
            },
            "endpoint": {
                "type": "string",
                "description": "Which endpoint provider to use (e.g., 'gemini', 'llama_cpp'). Defaults to 'gemini'."
            }
        },
        "required": ["prompt"]
    }
)
async def run_subagent(
    context: ToolContext,
    prompt: str,
    personalized: bool = False,
    session_id: Optional[str] = None,
    endpoint: str = "gemini",
) -> str:
    from agent.gateway import gateway, GatewayAddress, GatewayRequest
    
    mode = "personalized" if personalized else "unbiased"
    
    try:
        if not session_id:
            session_id = f"subagent_{uuid.uuid4().hex[:8]}"
            resume = False
        else:
            resume = True
            
        req = GatewayRequest(
            address=GatewayAddress(platform="subagent", session=session_id),
            text=prompt,
            resume=resume,
            mode=mode
        )
        
        # We assume the gateway is already configured and initialized.
        resp = await gateway.handle(req)
        return f"Subagent (session {session_id}) completed task. Response:\n{resp.text}"
    except Exception as e:
        return f"Error running subagent: {str(e)}"
