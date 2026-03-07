import json
import os
from typing import Dict, List, Tuple

from utils.utils import run_command

FAUGUS_FLATPAK_ID = "io.github.Faugus.faugus-launcher"
FAUGUS_CONFIG_DIR = os.path.expanduser(
    "~/.var/app/io.github.Faugus.faugus-launcher/config/faugus-launcher"
)
FAUGUS_DATA_DIR = os.path.expanduser(
    "~/.var/app/io.github.Faugus.faugus-launcher/data/faugus-launcher"
)
FAUGUS_GAMES_PATH = os.path.join(FAUGUS_CONFIG_DIR, "games.json")
FAUGUS_UMU_RUN_PATH = os.path.join(FAUGUS_DATA_DIR, "umu-run")
FAUGUS_EAC_PATH = os.path.join(FAUGUS_CONFIG_DIR, "components", "eac")
FAUGUS_BATTLEYE_PATH = os.path.join(FAUGUS_CONFIG_DIR, "components", "be")
FAUGUS_CONFIG_PATH = os.path.join(FAUGUS_CONFIG_DIR, "config.ini")


def detect_faugus_installation() -> bool:
    """Detect if Faugus is installed via Flatpak."""
    return run_command(f"flatpak list | grep {FAUGUS_FLATPAK_ID}").returncode == 0


def _parse_bool(value) -> bool | None:
    """Parse a Faugus boolean-ish value while preserving empty as None."""
    if isinstance(value, bool):
        return value
    if value is None:
        return None

    normalized = str(value).strip().strip('"').lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    if normalized == "":
        return None
    return None


def _load_faugus_defaults() -> Dict[str, bool]:
    """Load global defaults from the Faugus config file."""
    defaults = {
        "mangohud": False,
        "disable_hidraw": False,
        "prevent_sleep": False,
    }

    if not os.path.exists(FAUGUS_CONFIG_PATH):
        return defaults

    key_map = {
        "mangohud": "mangohud",
        "disable-hidraw": "disable_hidraw",
        "prevent-sleep": "prevent_sleep",
    }

    try:
        with open(FAUGUS_CONFIG_PATH, "r", encoding="utf-8", errors="ignore") as file:
            for raw_line in file:
                line = raw_line.strip()
                if not line or "=" not in line:
                    continue

                key, value = line.split("=", 1)
                normalized_key = key.strip()
                if normalized_key not in key_map:
                    continue

                parsed = _parse_bool(value)
                if parsed is not None:
                    defaults[key_map[normalized_key]] = parsed
    except OSError:
        pass

    return defaults


def list_faugus_games() -> List[Tuple[str, str, str, Dict[str, object]]]:
    """List games registered in the Faugus Flatpak installation."""
    games: List[Tuple[str, str, str, Dict[str, object]]] = []
    defaults = _load_faugus_defaults()

    if not os.path.exists(FAUGUS_GAMES_PATH):
        return games

    try:
        with open(FAUGUS_GAMES_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        print(f"Error parsing JSON file at {FAUGUS_GAMES_PATH}")
        return games

    if not isinstance(data, list):
        return games

    for entry in data:
        if not isinstance(entry, dict):
            continue

        if entry.get("hidden"):
            continue

        game_id = entry.get("gameid")
        title = entry.get("title")
        path = entry.get("path")
        prefix = entry.get("prefix")

        if not all(isinstance(value, str) and value.strip() for value in (game_id, title, path, prefix)):
            continue

        runner = {
            "type": "Faugus",
            "game_path": path.strip(),
            "prefix": prefix.strip(),
            "mangohud": defaults["mangohud"],
            "disable_hidraw": defaults["disable_hidraw"],
            "prevent_sleep": defaults["prevent_sleep"],
        }

        for key in ("mangohud", "disable_hidraw", "prevent_sleep"):
            parsed = _parse_bool(entry.get(key))
            if parsed is not None:
                runner[key] = parsed

        games.append((game_id.strip(), title.strip(), "Faugus", runner))

    return games
