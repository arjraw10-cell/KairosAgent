from __future__ import annotations

import subprocess

from agent.security import resolve_path
from agent.tooling import ToolContext, tool


@tool(
    name="run_powershell",
    description="Run a PowerShell command and return stdout, stderr, and exit code.",
    input_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout_sec": {"type": "integer", "default": 30, "minimum": 1},
            "cwd": {"type": "string"},
            "max_output_chars": {"type": "integer", "default": 12000, "minimum": 1},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
)
def run_powershell(
    context: ToolContext,
    command: str,
    timeout_sec: int = 30,
    cwd: str | None = None,
    max_output_chars: int = 12000,
) -> dict:
    working_dir = resolve_path(context.root_dir, cwd) if cwd else context.root_dir.resolve()
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        cwd=str(working_dir),
        check=False,
    )

    stdout = completed.stdout
    stderr = completed.stderr
    truncated = False
    if len(stdout) > max_output_chars:
        stdout = stdout[:max_output_chars]
        truncated = True
    if len(stderr) > max_output_chars:
        stderr = stderr[:max_output_chars]
        truncated = True

    return {
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": truncated,
        "cwd": str(working_dir),
    }
