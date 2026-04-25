from __future__ import annotations

from pathlib import Path


def _ensure_within_root(candidate: Path, root: Path, user_path: str) -> None:
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes root directory: {user_path}") from exc


def is_within_roots(candidate: Path, roots: list[Path]) -> bool:
    resolved_candidate = candidate.resolve()
    for root in roots:
        try:
            resolved_candidate.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def resolve_path(root_dir: Path, user_path: str) -> Path:
    root = root_dir.resolve()
    candidate = (root / user_path).resolve()
    _ensure_within_root(candidate, root, user_path)
    return candidate


def resolve_path_from_base(base_dir: Path, user_path: str, allowed_roots: list[Path]) -> Path:
    resolved_roots = [root.resolve() for root in allowed_roots]
    resolved_base = base_dir.resolve()
    if not is_within_roots(resolved_base, resolved_roots):
        raise ValueError(f"Base directory is outside allowed roots: {base_dir}")

    raw_path = Path(user_path)
    candidate = raw_path if raw_path.is_absolute() else (resolved_base / raw_path)
    resolved_candidate = candidate.resolve()
    if not is_within_roots(resolved_candidate, resolved_roots):
        raise ValueError(f"Path escapes allowed roots: {user_path}")
    return resolved_candidate
