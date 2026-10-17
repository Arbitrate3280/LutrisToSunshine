import os
import re
import shutil
import shlex
from typing import NamedTuple, Tuple, List, Optional
from config.types import DetectHints, GameSelection, LauncherSource
from utils.utils import normalize_detect_hints, run_command

STEAM_FLATPAK_ID = "com.valvesoftware.Steam"


def build_steam_command(game: GameSelection) -> Optional[str]:
    command = get_steam_command()
    if not command:
        return None
    return shlex.join([*shlex.split(command), f"steam://run/{game.game_id}"])

def detect_steam_installation() -> Tuple[bool, str]:
    """Detect if Steam is installed and how."""
    # Check for Flatpak installation
    if run_command(["flatpak", "info", STEAM_FLATPAK_ID]).returncode == 0:
        return True, "flatpak"
    elif shutil.which("steam"):
        return True, "native"
    else:
        return False, ""

def get_steam_root(installation_type: str) -> str:
    """Get the Steam root directory based on installation type."""
    if installation_type == "flatpak":
        return os.path.expanduser("~/.var/app/com.valvesoftware.Steam/.steam/steam")
    else:
        return os.path.expanduser("~/.steam/steam")

def parse_vdf_value(line: str) -> Optional[str]:
    """Parse a VDF value from a line like '"key" "value"'."""
    match = re.match(r'^\s*"[^"]*"\s*"([^"]*)"', line)
    return match.group(1) if match else None

def parse_libraryfolders(vdf_path: str) -> List[str]:
    """Parse libraryfolders.vdf to get library paths."""
    if not os.path.exists(vdf_path):
        return []
    
    paths = []
    with open(vdf_path, 'r') as f:
        content = f.read()
    
    # Simple parsing for library folders
    # Look for "path" entries
    for match in re.finditer(r'"path"\s*"([^"]*)"', content):
        paths.append(match.group(1))
    
    return paths

class SteamManifest(NamedTuple):
    appid: str
    name: str
    installdir: str


def parse_appmanifest(manifest_path: str) -> Optional[SteamManifest]:
    """Parse appmanifest_*.acf to get appid, name, and install dir."""
    if not os.path.exists(manifest_path):
        return None

    appid = None
    name = None
    installdir = None
    with open(manifest_path, 'r') as f:
        for line in f:
            if appid is None and '"appid"' in line:
                appid = parse_vdf_value(line)
            elif name is None and '"name"' in line:
                name = parse_vdf_value(line)
            elif installdir is None and '"installdir"' in line:
                installdir = parse_vdf_value(line)
            if appid and name and installdir:
                break

    if appid and name:
        return SteamManifest(appid, name, installdir or "")
    return None


def _list_steam_manifests() -> List[Tuple[str, SteamManifest]]:
    """(library path, manifest) for every installed Steam game."""
    installed, installation_type = detect_steam_installation()
    if not installed:
        return []

    steam_root = get_steam_root(installation_type)
    libraryfolders_path = os.path.join(steam_root, "config", "libraryfolders.vdf")

    library_paths = parse_libraryfolders(libraryfolders_path) or [steam_root]

    manifests: List[Tuple[str, SteamManifest]] = []
    exclude_patterns = ["proton", "steam linux runtime", "steamworks common", "steamvr"]
    for lib_path in library_paths:
        steamapps_path = os.path.join(lib_path, "steamapps")
        if not os.path.exists(steamapps_path):
            continue
        for filename in os.listdir(steamapps_path):
            if not (filename.startswith("appmanifest_") and filename.endswith(".acf")):
                continue
            manifest = parse_appmanifest(os.path.join(steamapps_path, filename))
            if manifest is None:
                continue
            # Filter out non-game items
            if any(manifest.name.lower().startswith(pattern) for pattern in exclude_patterns):
                continue
            manifests.append((lib_path, manifest))

    return manifests


def list_steam_games() -> List[Tuple[str, str]]:
    """List all Steam games."""
    return [(manifest.appid, manifest.name) for _, manifest in _list_steam_manifests()]


def _steam_detect_hints(library_path: str, manifest: SteamManifest) -> Optional[DetectHints]:
    hints: DetectHints = {}
    try:
        hints["steam_appid"] = int(manifest.appid)
    except ValueError:
        pass
    if manifest.installdir:
        hints["install_dir"] = os.path.join(
            library_path, "steamapps", "common", manifest.installdir
        )
    return normalize_detect_hints(hints)


def list_steam_selections() -> List[GameSelection]:
    source = LauncherSource("Steam")
    return [
        GameSelection(
            manifest.appid,
            manifest.name,
            "Steam",
            source,
            _steam_detect_hints(library_path, manifest),
        )
        for library_path, manifest in _list_steam_manifests()
    ]

def get_steam_command() -> str:
    """Get the command to run Steam."""
    installed, installation_type = detect_steam_installation()
    if not installed:
        return ""
    
    if installation_type == "flatpak":
        return f"flatpak run {STEAM_FLATPAK_ID}"
    else:
        return "steam"
