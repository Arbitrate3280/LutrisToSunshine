import json
import os
from typing import List, Tuple, Optional, Dict
from utils.utils import run_command

RETROARCH_FLATPAK_ID = "org.libretro.RetroArch"

def detect_retroarch_installation() -> bool:
    """Detect if RetroArch is installed via Flatpak or native."""
    # Check for Flatpak installation
    if run_command(f"flatpak list | grep {RETROARCH_FLATPAK_ID}").returncode == 0:
        return True
    # Check for native installation
    elif run_command("which retroarch").returncode == 0:
        return True
    else:
        return False

def get_retroarch_config_path() -> str:
    """Get the RetroArch config directory based on installation type."""
    # Check for Flatpak first
    if run_command(f"flatpak list | grep {RETROARCH_FLATPAK_ID}").returncode == 0:
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


def get_retroarch_cores_directory() -> str:
    """Resolve the directory where RetroArch cores are stored."""
    configured = _parse_config_value("libretro_directory")
    if configured:
        return configured

    config_path = get_retroarch_config_path()
    default_dir = os.path.join(config_path, "cores")
    if os.path.isdir(default_dir):
        return default_dir

    # Flatpak ships cores under /app directories when launched through flatpak
    # but from the host we do not have direct access; still, passing this path
    # through works for the launched process.
    if run_command(f"flatpak list | grep {RETROARCH_FLATPAK_ID}").returncode == 0:
        return "/app/libretro"

    return default_dir


def resolve_core_path(core_path: Optional[str], core_name: Optional[str]) -> Optional[str]:
    """Determine the actual core path to use when launching RetroArch."""
    if core_path:
        expanded = os.path.expanduser(core_path)
        if expanded.upper() not in ("DETECT", "", "NULL"):
            return expanded

    if not core_name:
        return None

    cores_dir = get_retroarch_cores_directory()
    sanitized = (
        core_name.lower()
        .replace(" ", "_")
        .replace("-", "_")
        .replace("(", "")
        .replace(")", "")
    )
    sanitized_compact = sanitized.replace("_", "")

    candidate_files = [
        f"{sanitized}_libretro.so",
        f"{sanitized}.so",
    ]

    if cores_dir:
        for candidate in candidate_files:
            candidate_path = candidate if os.path.isabs(candidate) else os.path.join(cores_dir, candidate)
            if os.path.isfile(candidate_path):
                return candidate_path

        if os.path.isdir(cores_dir):
            for entry in os.listdir(cores_dir):
                if not entry.endswith(".so"):
                    continue
                entry_compact = entry.lower().replace("_", "").replace("-", "").replace(" ", "")
                if sanitized_compact in entry_compact:
                    return os.path.join(cores_dir, entry)

        # As a last resort, return the guessed path even if it cannot be verified
        first_candidate = candidate_files[0]
        return first_candidate if os.path.isabs(first_candidate) else os.path.join(cores_dir, first_candidate)

    return None

def get_retroarch_command() -> str:
    """Get the RetroArch command based on installation type."""
    # Check for Flatpak installation
    if run_command(f"flatpak list | grep {RETROARCH_FLATPAK_ID}").returncode == 0:
        return f"flatpak run {RETROARCH_FLATPAK_ID}"
    # Check for native installation
    elif run_command("which retroarch").returncode == 0:
        return "retroarch"
    else:
        return ""

def list_retroarch_games() -> List[Tuple[str, str, Dict[str, str]]]:
    """List all games in RetroArch playlists."""
    games: List[Tuple[str, str, Dict[str, str]]] = []
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
                                if game_path and game_name:
                                    games.append((
                                        game_path,
                                        game_name,
                                        {
                                            "type": "RetroArch",
                                            "core_path": core_path or "",
                                            "core_name": core_name or "",
                                        },
                                    ))
            except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
                continue

    return games