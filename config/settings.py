"""Tool-owned settings store: ``~/.config/lutristosunshine/settings.json``.

Single persistence point for tool preferences that are independent of any
one Sunshine installation (saved server connections, install choice,
excluded game sources).  Unknown keys are preserved on every write.
"""

import json
import os
from typing import Any

SETTINGS_DIR = os.path.expanduser("~/.config/lutristosunshine")
SETTINGS_FILENAME = "settings.json"


def settings_path() -> str:
    return os.path.join(SETTINGS_DIR, SETTINGS_FILENAME)


def load_settings() -> dict:
    """Return the settings dict, or ``{}`` when missing or corrupt."""
    try:
        with open(settings_path(), encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_settings(settings: dict) -> None:
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    with open(settings_path(), "w", encoding="utf-8") as file:
        json.dump(settings, file, indent=2)


def update_setting(key: str, value: Any) -> None:
    """Set one key, preserving all other keys."""
    settings = load_settings()
    settings[key] = value
    save_settings(settings)
