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
        "games_json": os.path.join(config_dir, "games.json"),
        "config_ini": os.path.join(config_dir, "config.ini"),
        "umu_run": os.path.join(data_dir, "umu-run"),
        "eac": os.path.join(config_dir, "components", "eac"),
        "be": os.path.join(config_dir, "components", "be"),
    }


def get_faugus_command() -> str:
    """Get the command to run Faugus games."""
    install_type = get_faugus_installation_type()
    if install_type == "flatpak":
        return f"flatpak run --command=faugus-run {FAUGUS_FLATPAK_ID}"
    return "faugus-run"


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

    paths = get_faugus_paths()
    config_path = paths["config_ini"]

    if not os.path.exists(config_path):
        return defaults

    key_map = {
        "mangohud": "mangohud",
        "disable-hidraw": "disable_hidraw",
        "prevent-sleep": "prevent_sleep",
    }

    try:
        with open(config_path, "r", encoding="utf-8", errors="ignore") as file:
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
            "disable_hidraw": defaults["disable_hidraw"],
            "prevent_sleep": defaults["prevent_sleep"],
        }

        for key in ("mangohud", "disable_hidraw", "prevent_sleep"):
            parsed = _parse_bool(entry.get(key))
            if parsed is not None:
                runner[key] = parsed

        games.append((game_id.strip(), title.strip(), "Faugus", runner))

    return games
