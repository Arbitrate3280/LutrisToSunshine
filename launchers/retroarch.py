import json
import os
import shutil
import shlex
from typing import List, Tuple, Optional
from config.types import GameSelection, RetroArchSource
from utils.utils import run_command

RETROARCH_FLATPAK_ID = "org.libretro.RetroArch"


def build_retroarch_command(game: GameSelection) -> Optional[str]:
    if not isinstance(game.source, RetroArchSource):
        return None
    core_path = os.path.expanduser(game.source.core_path)
    command = get_retroarch_command()
    if not command or not core_path:
        return None
    return shlex.join([*shlex.split(command), "-L", core_path, game.game_id])


def validate_retroarch_selection(game: GameSelection) -> Optional[str]:
    if not isinstance(game.source, RetroArchSource):
        return "RetroArch core not set"
    core_path = game.source.core_path.strip()
    core_name = game.source.core_name.strip()
    if core_path.upper() == "DETECT" or core_name.upper() == "DETECT" or not core_path:
        return "RetroArch core not set"
    return None

def detect_retroarch_installation() -> bool:
    """Detect if RetroArch is installed via Flatpak or native."""
    # Check for Flatpak installation
    if run_command(["flatpak", "info", RETROARCH_FLATPAK_ID]).returncode == 0:
        return True
    elif shutil.which("retroarch"):
        return True
    else:
        return False

def get_retroarch_config_path() -> str:
    """Get the RetroArch config directory based on installation type."""
    # Check for Flatpak first
    if run_command(["flatpak", "info", RETROARCH_FLATPAK_ID]).returncode == 0:
        return os.path.expanduser("~/.var/app/org.libretro.RetroArch/config/retroarch")
    else:
        return os.path.expanduser("~/.config/retroarch")


def _parse_config_value(setting: str) -> Optional[str]:
    """Extract a value from retroarch.cfg if present."""
    config_path = get_retroarch_config_path()
    config_file = os.path.join(config_path, "retroarch.cfg")

    if not os.path.exists(config_file):
        return None

    with open(config_file, "r", encoding="utf-8", errors="ignore") as cfg:
        prefix = f"{setting}"
        for raw_line in cfg:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(prefix):
                parts = line.split("=", 1)
                if len(parts) == 2:
                    value = parts[1].strip().strip('"')
                    if value:
                        return os.path.expanduser(value)
    return None


def get_retroarch_playlist_directory() -> str:
    """Get the RetroArch playlist directory from config file or fallback."""
    configured = _parse_config_value("playlist_directory")
    if configured:
        return configured

    config_path = get_retroarch_config_path()
    return os.path.join(config_path, "playlists")


def get_retroarch_command() -> str:
    """Get the RetroArch command based on installation type."""
    # Check for Flatpak installation
    if run_command(["flatpak", "info", RETROARCH_FLATPAK_ID]).returncode == 0:
        return f"flatpak run {RETROARCH_FLATPAK_ID}"
    elif shutil.which("retroarch"):
        return "retroarch"
    else:
        return ""

def list_retroarch_games() -> List[Tuple[str, str, RetroArchSource]]:
    """List all games in RetroArch playlists."""
    games: List[Tuple[str, str, RetroArchSource]] = []
    playlists_path = get_retroarch_playlist_directory()

    if not os.path.exists(playlists_path):
        return games

    for filename in os.listdir(playlists_path):
        if filename.endswith(".lpl"):
            playlist_path = os.path.join(playlists_path, filename)
            try:
                with open(playlist_path, 'r', encoding='utf-8') as f:
                    playlist = json.load(f)
                    if "items" in playlist:
                        for item in playlist["items"]:
                            if isinstance(item, dict) and "path" in item and "label" in item:
                                game_path = item["path"]
                                game_name = item["label"]
                                core_path = item.get("core_path", "")
                                core_name = item.get("core_name", "")
                                if (
                                    isinstance(game_path, str)
                                    and isinstance(game_name, str)
                                    and isinstance(core_path, str)
                                    and isinstance(core_name, str)
                                    and game_path
                                    and game_name
                                ):
                                    games.append((
                                        game_path,
                                        game_name,
                                        RetroArchSource(core_path, core_name),
                                    ))
            except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
                continue

    return games


def list_retroarch_selections() -> List[GameSelection]:
    return [
        GameSelection(path, name, "RetroArch", core)
        for path, name, core in list_retroarch_games()
    ]
