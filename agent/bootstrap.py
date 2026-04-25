from __future__ import annotations

from pathlib import Path


CUSTOMIZATION_FILENAMES = (
    "memory.md",
    "user-preferences.md",
    "user.md",
    "identity.md",
)


def ensure_customization_files(agent_home_dir: Path) -> Path:
    root = (agent_home_dir / "customization").resolve()
    root.mkdir(parents=True, exist_ok=True)

    gitkeep_path = root / ".gitkeep"
    if not gitkeep_path.exists():
        gitkeep_path.write_text("", encoding="utf-8")

    for filename in CUSTOMIZATION_FILENAMES:
        file_path = root / filename
        if not file_path.exists():
            file_path.write_text("", encoding="utf-8")

    return root
