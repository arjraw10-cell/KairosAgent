from __future__ import annotations

import subprocess
import time
import os
from typing import Any

from agent.tooling import ToolContext, tool


@tool(
    name="shell",
    description="Run a shell command with strict timeout, working directory control, and isolated stdout/stderr.",
    input_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The command to run."},
            "cwd": {"type": "string", "description": "Working directory. Defaults to the agent project root."},
            "timeout": {"type": "integer", "default": 30, "minimum": 1, "description": "Timeout in seconds."},
            "env": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Additional environment variables to inject."
            },
            "max_output_chars": {"type": "integer", "default": 20000}
        },
        "required": ["command"],
        "additionalProperties": False,
    },
)
def shell(
    context: ToolContext,
    command: str,
    cwd: str | None = None,
    timeout: int = 30,
    env: dict[str, str] | None = None,
    max_output_chars: int = 20000,
) -> dict[str, Any]:
    working_dir = context.resolve_path(cwd) if cwd else context.current_dir()
    
    current_env = os.environ.copy()
    if env:
        for k, v in env.items():
            current_env[k] = str(v)
            
    start_time = time.time()
    
    timed_out = False
    stdout = ""
    stderr = ""
    exit_code = -1
    
    try:
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=str(working_dir),
            env=current_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        
        try:
            out, err = process.communicate(timeout=timeout)
            stdout = out
            stderr = err
            exit_code = process.returncode
        except subprocess.TimeoutExpired:
            process.kill()
            out, err = process.communicate()
            stdout = out
            stderr = err
            timed_out = True
            
    except Exception as e:
        stderr = f"Failed to start command process: {e}"
        
    duration_ms = int((time.time() - start_time) * 1000)
    
    stdout_truncated = False
    stderr_truncated = False
    
    if len(stdout) > max_output_chars:
        stdout = stdout[:max_output_chars]
        stdout_truncated = True
    if len(stderr) > max_output_chars:
        stderr = stderr[:max_output_chars]
        stderr_truncated = True

    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "cwd": str(working_dir),
        "duration_ms": duration_ms
    }
