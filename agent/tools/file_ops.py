from __future__ import annotations

import difflib
from agent.tooling import ToolContext, tool

@tool(
    name="read_file",
    description="Read a text file from the agent root directory. Returns content with line numbers prepended.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to read."},
            "line_start": {"type": "integer", "minimum": 1, "description": "Optional start line number."},
            "line_end": {"type": "integer", "minimum": 1, "description": "Optional end line number."},
            "max_lines": {"type": "integer", "default": 1000, "minimum": 1, "description": "Hard cap on lines returned."},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
)
def read_file(
    context: ToolContext,
    path: str,
    line_start: int | None = None,
    line_end: int | None = None,
    max_lines: int = 1000,
) -> dict:
    try:
        abs_path = context.resolve_path(path)
        if not abs_path.exists():
            return {"error": f"File not found: {path}", "content": None, "total_lines": 0, "returned_lines": [], "truncated": False, "file_size_bytes": 0}
        
        file_size_bytes = abs_path.stat().st_size
        try:
            content_str = abs_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {"error": f"File is not utf-8 encoded text: {path}", "content": None, "total_lines": 0, "returned_lines": [], "truncated": False, "file_size_bytes": file_size_bytes}

        lines = content_str.splitlines()
        total_lines = len(lines)
        
        start_idx = max(0, (line_start - 1) if line_start else 0)
        end_idx = min(total_lines, line_end if line_end else total_lines)
        
        if end_idx <= start_idx:
            start_idx = 0
            end_idx = total_lines
            
        selected_lines = lines[start_idx:end_idx]
        truncated = False
        if len(selected_lines) > max_lines:
            selected_lines = selected_lines[:max_lines]
            end_idx = start_idx + max_lines
            truncated = True
            
        numbered_lines = []
        for i, line in enumerate(selected_lines):
            line_num = start_idx + i + 1
            numbered_lines.append(f"{line_num:4} | {line}")
            
        return {
            "content": "\n".join(numbered_lines),
            "total_lines": total_lines,
            "returned_lines": [start_idx + 1, end_idx],
            "truncated": truncated,
            "file_size_bytes": file_size_bytes,
            "error": None
        }
    except Exception as e:
        return {"error": str(e), "content": None, "total_lines": 0, "returned_lines": [], "truncated": False, "file_size_bytes": 0}


@tool(
    name="write_file",
    description="Write text content to a file. Use mode 'create_only' (default), 'overwrite', or 'append'.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "mode": {"type": "string", "enum": ["create_only", "overwrite", "append"], "default": "create_only"},
            "encoding": {"type": "string", "default": "utf-8"},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    },
)
def write_file(
    context: ToolContext,
    path: str,
    content: str,
    mode: str = "create_only",
    encoding: str = "utf-8"
) -> dict:
    try:
        abs_path = context.resolve_path(path)
        existed = abs_path.exists()
        
        if mode == "create_only" and existed:
            return {"success": False, "path": str(abs_path), "bytes_written": 0, "lines_written": 0, "already_existed": True, "dirs_created": [], "error": f"File already exists: {path}. Use mode 'overwrite' to overwrite."}
            
        dirs_created = []
        curr = abs_path.parent
        # Try to find which directories actually needed creation
        while not curr.exists() and curr != curr.parent:
            dirs_created.append(str(curr))
            curr = curr.parent
        dirs_created.reverse()
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        
        if mode == "append":
            with open(abs_path, "a", encoding=encoding) as f:
                f.write(content)
        else:
            abs_path.write_text(content, encoding=encoding)
            
        return {
            "success": True,
            "path": str(abs_path),
            "bytes_written": len(content.encode(encoding)),
            "lines_written": len(content.splitlines()),
            "already_existed": existed,
            "dirs_created": dirs_created,
            "error": None
        }
    except Exception as e:
        return {"success": False, "path": path, "bytes_written": 0, "lines_written": 0, "already_existed": False, "dirs_created": [], "error": str(e)}


@tool(
    name="edit_file",
    description="Edit a file by exactly replacing old_str with new_str. Returns failure if 0 or >1 matches.",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_str": {"type": "string"},
            "new_str": {"type": "string"},
            "context_lines": {"type": "integer", "default": 5, "minimum": 0},
            "encoding": {"type": "string", "default": "utf-8"},
        },
        "required": ["path", "old_str", "new_str"],
        "additionalProperties": False,
    },
)
def edit_file(
    context: ToolContext,
    path: str,
    old_str: str,
    new_str: str,
    context_lines: int = 5,
    encoding: str = "utf-8",
) -> dict:
    try:
        abs_path = context.resolve_path(path)
        if not abs_path.exists():
            return {
                "success": False,
                "path": str(abs_path),
                "replacements_made": 0,
                "after_context": "",
                "error": f"File not found: {path}"
            }
            
        if old_str == "":
            return {
                "success": False,
                "path": str(abs_path),
                "replacements_made": 0,
                "after_context": "",
                "error": "old_str cannot be empty"
            }

        original = abs_path.read_text(encoding=encoding)
        matches = []
        search_pos = 0
        while True:
            idx = original.find(old_str, search_pos)
            if idx == -1:
                break
            matches.append(idx)
            search_pos = idx + len(old_str)
            
        if len(matches) == 0:
            lines = original.splitlines()
            old_lines = old_str.splitlines()
            snippet = "No close match found."
            if old_lines:
                # Use a larger chunk if possible
                first_line = old_lines[0].strip()
                if not first_line and len(old_lines) > 1:
                    first_line = old_lines[1].strip()
                matches_close = difflib.get_close_matches(first_line, [l.strip() for l in lines if l.strip()], n=3, cutoff=0.6)
                if matches_close:
                    for i, l in enumerate(lines):
                        if l.strip() in matches_close:
                            start_l = max(0, i - 3)
                            end_l = min(len(lines), i + 4)
                            snip_lines = []
                            for j in range(start_l, end_l):
                                snip_lines.append(f"{j+1:4} | {lines[j]}")
                            snippet = "\n".join(snip_lines)
                            break
            
            error_msg = f"Zero matches found for old_str. File content near potential match:\n{snippet}\nCheck for formatting, whitespace, or if it was already updated."
            return {
                "success": False,
                "path": str(abs_path),
                "replacements_made": 0,
                "after_context": "",
                "error": error_msg
            }
            
        if len(matches) > 1:
            return {
                "success": False,
                "path": str(abs_path),
                "replacements_made": 0,
                "after_context": "",
                "error": f"Found {len(matches)} occurrences. Be more specific with old_str."
            }
            
        start = matches[0]
        end = start + len(old_str)
        updated = original[:start] + new_str + original[end:]
        abs_path.write_text(updated, encoding=encoding)
        
        lines_before = original[:start].count('\\n')
        new_lines_count = new_str.count('\\n')
        
        updated_list = updated.splitlines()
        start_context_idx = max(0, lines_before - context_lines)
        end_context_idx = min(len(updated_list), lines_before + new_lines_count + 1 + context_lines)
        
        snip_lines = []
        for j in range(start_context_idx, end_context_idx):
            snip_lines.append(f"{j+1:4} | {updated_list[j]}")
        
        after_context = "\n".join(snip_lines)
        
        return {
            "success": True,
            "path": str(abs_path),
            "replacements_made": 1,
            "after_context": after_context,
            "error": None
        }
    except Exception as e:
         return {
            "success": False,
            "path": path,
            "replacements_made": 0,
            "after_context": "",
            "error": str(e)
        }
