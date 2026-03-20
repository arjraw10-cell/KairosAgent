from __future__ import annotations

from agent.security import resolve_path
from agent.tooling import ToolContext, tool


@tool(
    name="read_file",
    description="Read a text file from the agent root directory.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "encoding": {"type": "string", "default": "utf-8"},
            "max_chars": {"type": "integer", "default": 200000, "minimum": 1},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
)
def read_file(context: ToolContext, path: str, encoding: str = "utf-8", max_chars: int = 200000) -> dict:
    abs_path = resolve_path(context.root_dir, path)
    if not abs_path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    content = abs_path.read_text(encoding=encoding)
    truncated = False
    if len(content) > max_chars:
        content = content[:max_chars]
        truncated = True
    return {"path": str(abs_path), "content": content, "truncated": truncated}


@tool(
    name="write_file",
    description="Write text content to a file in the agent root directory.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "encoding": {"type": "string", "default": "utf-8"},
            "overwrite": {"type": "boolean", "default": True},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    },
)
def write_file(
    context: ToolContext,
    path: str,
    content: str,
    encoding: str = "utf-8",
    overwrite: bool = True,
) -> dict:
    abs_path = resolve_path(context.root_dir, path)
    if abs_path.exists() and not overwrite:
        raise FileExistsError(f"File already exists and overwrite is false: {path}")
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(content, encoding=encoding)
    return {"path": str(abs_path), "bytes_written": len(content.encode(encoding))}


@tool(
    name="edit_file",
    description=(
        "Replace target_block with new_block only when both before_context and after_context "
        "exactly match around that block."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "before_context": {"type": "string"},
            "target_block": {"type": "string"},
            "after_context": {"type": "string"},
            "new_block": {"type": "string"},
            "encoding": {"type": "string", "default": "utf-8"},
        },
        "required": ["path", "before_context", "target_block", "after_context", "new_block"],
        "additionalProperties": False,
    },
)
def edit_file(
    context: ToolContext,
    path: str,
    before_context: str,
    target_block: str,
    after_context: str,
    new_block: str,
    encoding: str = "utf-8",
) -> dict:
    abs_path = resolve_path(context.root_dir, path)
    if not abs_path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if target_block == "":
        raise ValueError("target_block cannot be empty")

    original = abs_path.read_text(encoding=encoding)
    candidate_starts: list[int] = []
    search_pos = 0

    while True:
        idx = original.find(target_block, search_pos)
        if idx == -1:
            break
        candidate_starts.append(idx)
        search_pos = idx + len(target_block)

    if not candidate_starts:
        raise ValueError("target_block was not found in file")

    matches: list[int] = []
    target_len = len(target_block)
    for start in candidate_starts:
        before_ok = True
        after_ok = True
        if before_context:
            before_ok = start >= len(before_context) and original[start - len(before_context) : start] == before_context
        if after_context:
            after_start = start + target_len
            after_end = after_start + len(after_context)
            after_ok = original[after_start:after_end] == after_context
        if before_ok and after_ok:
            matches.append(start)

    if not matches:
        raise ValueError(
            "Found target_block occurrences but none matched the provided before_context/after_context."
        )
    if len(matches) > 1:
        raise ValueError(
            f"Edit is ambiguous: {len(matches)} matches satisfy the supplied context. Provide narrower context."
        )

    start = matches[0]
    end = start + target_len
    updated = original[:start] + new_block + original[end:]
    abs_path.write_text(updated, encoding=encoding)

    return {
        "path": str(abs_path),
        "edited": True,
        "match_index": start,
        "old_length": len(target_block),
        "new_length": len(new_block),
    }
