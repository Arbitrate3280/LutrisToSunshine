import json
import os
import shutil
from typing import Dict, List, Tuple, Optional

from utils.utils import run_command

FAUGUS_FLATPAK_ID = "io.github.Faugus.faugus-launcher"


def get_faugus_installation_type() -> Optional[str]:
    """Detect how Faugus is installed."""
    # Check for Flatpak
    if run_command(f"flatpak list | grep {FAUGUS_FLATPAK_ID}").returncode == 0:
        return "flatpak"

    # Check for Native
    if shutil.which("faugus-launcher") or os.path.exists(
        os.path.expanduser("~/.config/faugus-launcher/games.json")
    ):
        return "native"

    return None


def detect_faugus_installation() -> bool:
    """Detect if Faugus is installed via Flatpak or natively."""
    return get_faugus_installation_type() is not None


def get_faugus_paths() -> Dict[str, str]:
    """Get the appropriate Faugus paths based on installation type."""
    install_type = get_faugus_installation_type()

    if install_type == "flatpak":
        config_dir = os.path.expanduser(
            "~/.var/app/io.github.Faugus.faugus-launcher/config/faugus-launcher"
        )
        data_dir = os.path.expanduser(
            "~/.var/app/io.github.Faugus.faugus-launcher/data/faugus-launcher"
        )
    else:
        # Native or fallback
        config_dir = os.path.expanduser("~/.config/faugus-launcher")
        data_dir = os.path.expanduser("~/.local/share/faugus-launcher")

    return {
        "config": config_dir,
        "data": data_dir,
        "games_json": os.path.join(data_dir, "games.json"),
        "config_json": os.path.join(config_dir, "config.json"),
        "umu_run": os.path.join(data_dir, "umu-run"),
        "eac": os.path.join(data_dir, "components", "eac"),
        "be": os.path.join(data_dir, "components", "be"),
    }


def get_faugus_command() -> str:
    """Get the command to run Faugus games."""
    install_type = get_faugus_installation_type()
    if install_type == "flatpak":
        return f"flatpak run --command=faugus-launcher {FAUGUS_FLATPAK_ID}"
    return "faugus-launcher"


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
        "gamemode": False,
    }

    paths = get_faugus_paths()
    config_path = paths["config_json"]

    if not os.path.exists(config_path):
        return defaults

    try:
        with open(config_path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return defaults

    if not isinstance(data, dict):
        return defaults

    for key in ("mangohud", "gamemode"):
        parsed = _parse_bool(data.get(key))
        if parsed is not None:
            defaults[key] = parsed

    return defaults


def list_faugus_games() -> List[Tuple[str, str, str, Dict[str, object]]]:
    """List games registered in the Faugus installation."""
    games: List[Tuple[str, str, str, Dict[str, object]]] = []
    defaults = _load_faugus_defaults()
    paths = get_faugus_paths()
    games_json_path = paths["games_json"]

    if not os.path.exists(games_json_path):
        return games

    try:
        with open(games_json_path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        print(f"Error parsing JSON file at {games_json_path}")
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

        if not all(
            isinstance(value, str) and value.strip()
            for value in (game_id, title, path, prefix)
        ):
            continue

        runner = {
            "type": "Faugus",
            "game_path": path.strip(),
            "prefix": prefix.strip(),
            "mangohud": defaults["mangohud"],
            "gamemode": defaults["gamemode"],
            "no_sleep": False,
            "sdl_enabled": False,
        }

        for key in ("mangohud", "gamemode", "no_sleep", "sdl_enabled"):
            parsed = _parse_bool(entry.get(key))
            if parsed is not None:
                runner[key] = parsed

        games.append((game_id.strip(), title.strip(), "Faugus", runner))

    return games
