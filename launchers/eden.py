import glob
import os
import re
from typing import List, Tuple

from utils.utils import run_command

SUPPORTED_EXTENSIONS = (".nsp", ".xci", ".nca", ".nro")
TITLE_ID_PATTERN = re.compile(r"\[([0-9A-Fa-f]{16})\]")


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
    """Get the Eden launch command (native binary or AppImage)."""
    if run_command("which eden").returncode == 0:
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


def get_eden_game_dirs() -> List[str]:
    """Get Eden game directories from qt-config.ini."""
    game_dirs: List[str] = []
    config_path = os.path.expanduser("~/.config/eden/qt-config.ini")

    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8", errors="ignore") as config:
                for raw_line in config:
                    line = raw_line.strip()
                    match = re.match(r"Paths\\gamedirs\\\d+\\path=(.+)", line)
                    if not match:
                        continue
                    path = os.path.expanduser(match.group(1).strip())
                    # Ignore Eden virtual entries like SDMC/UserNAND/SysNAND.
                    if os.path.isabs(path):
                        game_dirs.append(path)
        except OSError:
            pass

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
