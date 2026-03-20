from __future__ import annotations

from pathlib import Path


def resolve_path(root_dir: Path, user_path: str) -> Path:
    root = root_dir.resolve()
    candidate = (root / user_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes root directory: {user_path}") from exc
    return candidate
