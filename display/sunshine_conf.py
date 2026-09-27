"""Key/value access for Sunshine's ``sunshine.conf``.

The virtual display owns a few keys in Sunshine's own configuration file
(``audio_sink``, ``global_prep_cmd``, and -- in portal capture mode --
``capture``/``output_name``).  Those edits must never rewrite the rest of
the file: Sunshine's config is shared with the user's own settings, so
every helper here preserves unknown keys, comments, and ordering.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List


def read_key_value(path: Path, key: str) -> Dict[str, Any]:
    """Return ``{"present": bool, "value": str}`` for ``key`` in ``path``."""
    if not path.exists():
        return {"present": False, "value": ""}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        current_key, value = stripped.split("=", 1)
        if current_key.strip() == key:
            return {"present": True, "value": value.strip()}
    return {"present": False, "value": ""}


def set_key_value(path: Path, key: str, value: str) -> None:
    """Set ``key = value`` in ``path``, replacing the first match in place."""
    lines: List[str] = []
    found = False
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()

    updated_lines: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            current_key = stripped.split("=", 1)[0].strip()
            if current_key == key:
                updated_lines.append(f"{key} = {value}")
                found = True
                continue
        updated_lines.append(line)

    if not found:
        if updated_lines and updated_lines[-1] != "":
            updated_lines.append("")
        updated_lines.append(f"{key} = {value}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")


def remove_key(path: Path, key: str) -> None:
    """Drop every ``key = ...`` line from ``path``, keeping the rest."""
    if not path.exists():
        return
    updated_lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            current_key = stripped.split("=", 1)[0].strip()
            if current_key == key:
                continue
        updated_lines.append(line)
    path.write_text("\n".join(updated_lines).rstrip() + "\n", encoding="utf-8")


def remove_key_if_value(path: Path, key: str, expected: str) -> None:
    """Remove ``key`` only while it still holds the value this tool wrote.

    A user who replaced our managed value with their own keeps their setting
    when the virtual display is reset.
    """
    current = read_key_value(path, key)
    if current.get("present") and current.get("value") == expected:
        remove_key(path, key)
