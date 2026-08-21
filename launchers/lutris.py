import configparser
import os
import shutil
import shlex
import sqlite3
from typing import Optional, List, Tuple
from config.types import GameSelection, LauncherSource
from utils.utils import run_command

LUTRIS_FLATPAK_ID = "net.lutris.Lutris"


def build_lutris_command(game: GameSelection) -> Optional[str]:
    command = get_lutris_command()
    if not command:
        return None
    return shlex.join([*shlex.split(command), f"lutris:rungameid/{game.game_id}"])

def _is_flatpak_install() -> bool:
    return run_command(["flatpak", "info", LUTRIS_FLATPAK_ID]).returncode == 0


def get_lutris_command(args: str = "") -> Optional[str]:
    """Get the appropriate Lutris command based on installation type."""
    # Check for Flatpak installation
    if _is_flatpak_install():
        base_cmd = f"flatpak run {LUTRIS_FLATPAK_ID}"
    elif shutil.which("lutris"):
        base_cmd = "lutris"
    else:
        return None

    return f"{base_cmd} {args}".strip()

def is_lutris_running() -> bool:
    """Check if Lutris is currently running."""
    our_script_name = os.path.basename(__file__)
    result = run_command(["ps", "-A", "-o", "comm=", "-o", "args="])
    if result.returncode != 0:
        return False
    for line in (result.stdout or "").splitlines():
        if our_script_name in line:
            continue
        if "net.lutris.Lutris" in line:
            return True
        command = line.strip().split(maxsplit=1)[0] if line.strip() else ""
        if command == "lutris":
            return True
    return False

def _pga_db_candidates() -> List[str]:
    """Candidate paths for Lutris' PGA database (mirrors lutris.settings.DB_PATH:
    `pga_path` from any lutris.conf, then the default data/config locations).
    The active install's locations come first, matching get_lutris_command()."""
    home = os.path.expanduser("~")
    native_dirs = (
        os.path.join(home, ".local", "share", "lutris"),
        os.path.join(home, ".config", "lutris"),
    )
    fp = os.path.join(home, ".var", "app", LUTRIS_FLATPAK_ID)
    flatpak_dirs = (
        os.path.join(fp, "data", "lutris"),
        os.path.join(fp, "config", "lutris"),
    )
    data_dirs = flatpak_dirs + native_dirs if _is_flatpak_install() else native_dirs + flatpak_dirs
    candidates = []
    for conf_dir in data_dirs:
        parser = configparser.ConfigParser()
        try:
            parser.read(os.path.join(conf_dir, "lutris.conf"))
            override = parser.get("lutris", "pga_path", fallback=None)
        except configparser.Error:
            override = None
        if override:
            candidates.append(override)
    candidates += [os.path.join(d, "pga.db") for d in data_dirs]
    return candidates


def list_lutris_games() -> List[Tuple[str, str]]:
    """List all games by reading Lutris' PGA database directly — the same rows
    `lutris -lo --json` returns (lutris.gui.application calls get_games with no
    filters), without spawning the Lutris runtime."""
    for path in _pga_db_candidates():
        if not os.path.isfile(path):
            continue
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                rows = db.execute("SELECT id, name FROM games").fetchall()
            finally:
                db.close()
            return [(str(game_id), name) for game_id, name in rows]
        except sqlite3.Error:
            continue
    return []


def list_lutris_selections() -> List[GameSelection]:
    source = LauncherSource("Lutris")
    return [GameSelection(game_id, name, "Lutris", source) for game_id, name in list_lutris_games()]
