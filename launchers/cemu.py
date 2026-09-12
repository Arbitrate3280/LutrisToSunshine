import glob
import os
import shlex
import shutil
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

from config.types import GameSelection, LauncherSource
from utils.utils import run_command

CEMU_FLATPAK_ID = "info.cemu.Cemu"

KNOWN_EXTENSIONS = (".wud", ".wux", ".iso", ".wua", ".wuhb")
TITLE_SYSTEM_DIRS = ("content", "code", "meta")


def build_cemu_command(game: GameSelection) -> Optional[str]:
    command = get_cemu_command()
    if not command:
        return None
    return shlex.join([*shlex.split(command), "-f", "-g", game.game_id])


def _candidate_home_dirs() -> List[str]:
    """Return possible home roots for users that expose /home and /var/home."""
    home = os.path.expanduser("~")
    user = os.path.basename(home.rstrip("/"))
    candidates = [home, f"/home/{user}", f"/var/home/{user}"]

    unique: List[str] = []
    for path in candidates:
        if path not in unique:
            unique.append(path)
    return unique


def get_cemu_command() -> str:
    """Get the Cemu launch command (Flatpak, native binary, or AppImage)."""
    if run_command(["flatpak", "info", CEMU_FLATPAK_ID]).returncode == 0:
        return f"flatpak run {CEMU_FLATPAK_ID}"

    native = shutil.which("cemu")
    if native:
        return "cemu"

    patterns = [
        "AppImages/cemu.appimage",
        "AppImages/Cemu.appimage",
        "AppImages/*cemu*.appimage",
        "AppImages/*cemu*.AppImage",
        "cemu.appimage",
        "Cemu.appimage",
    ]

    for home_dir in _candidate_home_dirs():
        for pattern in patterns:
            for candidate in sorted(glob.glob(os.path.join(home_dir, pattern))):
                if os.path.isfile(candidate):
                    return candidate

    return ""


def detect_cemu_installation() -> bool:
    """Detect if Cemu is installed."""
    return bool(get_cemu_command())


def get_cemu_settings_paths() -> List[str]:
    """Candidate settings.xml paths, highest priority first."""
    home = os.path.expanduser("~")
    xdg_config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    candidates = [
        os.path.join(home, ".var", "app", CEMU_FLATPAK_ID, "config", "Cemu", "settings.xml"),
        os.path.join(xdg_config, "Cemu", "settings.xml"),
    ]

    # Portable mode keeps settings.xml in a "portable" dir next to the executable.
    command = get_cemu_command()
    if command and not command.startswith("flatpak"):
        binary = shlex.split(command)[0]
        if os.path.isfile(binary):
            sibling = os.path.join(
                os.path.dirname(os.path.abspath(binary)), "portable", "settings.xml"
            )
            if sibling not in candidates:
                candidates.append(sibling)

    return candidates


def _parse_settings_xml(path: str) -> Tuple[List[str], Dict[str, str]]:
    """Read game dirs and path->display-name cache from a settings.xml file."""
    game_dirs: List[str] = []
    names: Dict[str, str] = {}
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return game_dirs, names

    for game_paths in root.iter("GamePaths"):
        for entry in game_paths.iter("Entry"):
            if entry.text and entry.text.strip():
                game_dirs.append(os.path.expanduser(entry.text.strip()))

    for game_cache in root.iter("GameCache"):
        for entry in game_cache.iter("Entry"):
            path_text = entry.findtext("path", "").strip()
            if not path_text:
                continue
            display_name = (
                entry.findtext("custom_name", "").strip()
                or entry.findtext("name", "").strip()
            )
            if display_name:
                names[os.path.normpath(path_text)] = display_name

    return game_dirs, names


def get_cemu_game_dirs() -> List[str]:
    """Get Cemu game directories from every existing settings.xml."""
    game_dirs: List[str] = []
    for path in get_cemu_settings_paths():
        if not os.path.isfile(path):
            continue
        dirs, _ = _parse_settings_xml(path)
        for game_dir in dirs:
            if game_dir not in game_dirs:
                game_dirs.append(game_dir)
    return game_dirs


def _get_cemu_name_cache() -> Dict[str, str]:
    """Merge path->display-name caches from every existing settings.xml."""
    names: Dict[str, str] = {}
    for path in get_cemu_settings_paths():
        if not os.path.isfile(path):
            continue
        _, cached = _parse_settings_xml(path)
        names.update(cached)
    return names


def _is_title_folder(path: str) -> bool:
    """Mirror Cemu's ScanGamePath: a title folder has content+code+meta dirs."""
    try:
        entries = {entry.lower() for entry in os.listdir(path)}
    except OSError:
        return False
    return all(system_dir in entries for system_dir in TITLE_SYSTEM_DIRS)


def _fallback_name(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path.rstrip("/")))[0]
    return stem or os.path.basename(path.rstrip("/"))


def list_cemu_games() -> List[Tuple[str, str]]:
    """List games in Cemu game dirs, mirroring Cemu's own title scanner."""
    games: List[Tuple[str, str]] = []
    seen_paths = set()
    names = _get_cemu_name_cache()

    def add_game(game_path: str) -> None:
        normalized = os.path.normpath(game_path)
        if normalized in seen_paths:
            return
        seen_paths.add(normalized)
        games.append((game_path, names.get(normalized, _fallback_name(game_path))))

    for games_dir in get_cemu_game_dirs():
        if not os.path.isdir(games_dir):
            continue

        for root, dirs, files in os.walk(games_dir, topdown=True):
            if _is_title_folder(root):
                add_game(root)
                # Only descend into non-system subdirs, like Cemu does.
                dirs[:] = [d for d in dirs if d.lower() not in TITLE_SYSTEM_DIRS]
                continue

            for file_name in files:
                lowered = file_name.lower()
                if lowered == "title.tmd" or lowered.endswith(KNOWN_EXTENSIONS):
                    add_game(os.path.join(root, file_name))

    return games


def list_cemu_selections() -> List[GameSelection]:
    source = LauncherSource("Cemu")
    return [GameSelection(game_id, name, "Cemu", source) for game_id, name in list_cemu_games()]
