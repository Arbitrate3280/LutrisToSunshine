import glob
import os
import re
import shutil
from typing import List, Optional, Tuple
import shlex

from config.types import DetectHints, GameSelection, LauncherSource
from utils.utils import normalize_detect_hints, run_command

EDEN_FLATPAK_ID = "dev.eden_emu.eden"

SUPPORTED_EXTENSIONS = (".nsp", ".xci", ".nca", ".nro")
TITLE_ID_PATTERN = re.compile(r"\[([0-9A-Fa-f]{16})\]")


def build_eden_command(game: GameSelection) -> Optional[str]:
    command = get_eden_command()
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


def get_eden_command() -> str:
    """Get the Eden launch command (Flatpak, native binary, or AppImage)."""
    if run_command(["flatpak", "info", EDEN_FLATPAK_ID]).returncode == 0:
        return f"flatpak run {EDEN_FLATPAK_ID}"

    if shutil.which("eden"):
        return "eden"

    patterns = [
        "AppImages/eden.appimage",
        "AppImages/Eden.appimage",
        "AppImages/*eden*.appimage",
        "AppImages/*eden*.AppImage",
        "eden.appimage",
        "Eden.appimage",
    ]

    for home_dir in _candidate_home_dirs():
        for pattern in patterns:
            for candidate in sorted(glob.glob(os.path.join(home_dir, pattern))):
                if os.path.isfile(candidate):
                    return candidate

    return ""


def detect_eden_installation() -> bool:
    """Detect if Eden is installed."""
    return bool(get_eden_command())


def _is_base_game_file(file_name: str) -> bool:
    """Return True when the filename looks like a launchable base title."""
    stem = os.path.splitext(file_name)[0]
    stem_lower = stem.lower()

    if any(token in stem_lower for token in ("[update", "[upd]", "[dlc")):
        return False

    title_id_match = TITLE_ID_PATTERN.search(stem)
    if title_id_match:
        title_id = title_id_match.group(1).lower()
        if title_id[-3:] != "000":
            return False

    return True


def get_eden_qt_config_paths() -> List[str]:
    """Candidate qt-config.ini paths, highest priority first."""
    home = os.path.expanduser("~")
    xdg_config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    return [
        os.path.join(home, ".var", "app", EDEN_FLATPAK_ID, "config", "eden", "qt-config.ini"),
        os.path.join(xdg_config, "eden", "qt-config.ini"),
    ]


def _parse_qt_config_game_dirs(path: str) -> List[str]:
    """Read game directories from a single qt-config.ini file."""
    game_dirs: List[str] = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as config:
            for raw_line in config:
                line = raw_line.strip()
                match = re.match(r"Paths\\gamedirs\\\d+\\path=(.+)", line)
                if not match:
                    continue
                game_dir = os.path.expanduser(match.group(1).strip())
                # Ignore Eden virtual entries like SDMC/UserNAND/SysNAND.
                if os.path.isabs(game_dir):
                    game_dirs.append(game_dir)
    except OSError:
        pass
    return game_dirs


def get_eden_game_dirs() -> List[str]:
    """Get Eden game directories from every existing qt-config.ini."""
    game_dirs: List[str] = []
    for path in get_eden_qt_config_paths():
        if not os.path.isfile(path):
            continue
        for game_dir in _parse_qt_config_game_dirs(path):
            if game_dir not in game_dirs:
                game_dirs.append(game_dir)
    return game_dirs


def list_eden_games() -> List[Tuple[str, str]]:
    """List games available in Eden directories."""
    games: List[Tuple[str, str]] = []
    seen_paths = set()

    for games_dir in get_eden_game_dirs():
        if not os.path.exists(games_dir):
            continue

        for root, _, files in os.walk(games_dir):
            for file_name in files:
                if not file_name.lower().endswith(SUPPORTED_EXTENSIONS):
                    continue
                if not _is_base_game_file(file_name):
                    continue

                game_path = os.path.join(root, file_name)
                if game_path in seen_paths:
                    continue
                seen_paths.add(game_path)

                game_name = re.sub(r"\[.*?\]", "", os.path.splitext(file_name)[0]).strip()
                if not game_name:
                    game_name = os.path.splitext(file_name)[0]

                games.append((game_path, game_name))

    return games


def _eden_detect_hints() -> Optional[DetectHints]:
    command = get_eden_command()
    if not command:
        return None
    hints: DetectHints = {"process_name": "eden"}
    if command.startswith("flatpak"):
        hints["env_marker"] = {"key": "FLATPAK_ID", "value": EDEN_FLATPAK_ID}
    elif not os.path.isabs(command):
        resolved = shutil.which(command)
        if resolved:
            hints["exe"] = resolved
    return normalize_detect_hints(hints)


def list_eden_selections() -> List[GameSelection]:
    source = LauncherSource("Eden")
    hints = _eden_detect_hints()
    return [
        GameSelection(game_id, name, "Eden", source, hints)
        for game_id, name in list_eden_games()
    ]
