import configparser
import os
import shutil
import shlex
import sqlite3
from typing import Dict, Optional, List, Tuple

import yaml

from config.types import DetectHints, GameSelection, LauncherSource
from utils.utils import normalize_detect_hints, path_basename_for_hint, run_command

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

def _lutris_data_dirs() -> List[str]:
    """Lutris data/config roots, active install first (Flatpak vs native)."""
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
    order = flatpak_dirs + native_dirs if _is_flatpak_install() else native_dirs + flatpak_dirs
    return list(order)


def _pga_db_candidates() -> List[str]:
    """Candidate paths for Lutris' PGA database (mirrors lutris.settings.DB_PATH:
    `pga_path` from any lutris.conf, then the default data/config locations).
    The active install's locations come first, matching get_lutris_command()."""
    data_dirs = _lutris_data_dirs()
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


def _list_lutris_rows() -> List[Tuple[str, str, str, str, str]]:
    """(id, name, executable, directory, configpath) for every PGA row."""
    for path in _pga_db_candidates():
        if not os.path.isfile(path):
            continue
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                rows = db.execute(
                    "SELECT id, name, executable, directory, configpath FROM games"
                ).fetchall()
            finally:
                db.close()
            return [
                (
                    str(game_id),
                    name or "",
                    executable or "",
                    directory or "",
                    configpath or "",
                )
                for game_id, name, executable, directory, configpath in rows
            ]
        except sqlite3.Error:
            continue
    return []


def list_lutris_games() -> List[Tuple[str, str]]:
    """List all games by reading Lutris' PGA database directly — the same rows
    `lutris -lo --json` returns (lutris.gui.application calls get_games with no
    filters), without spawning the Lutris runtime."""
    return [(game_id, name) for game_id, name, *_ in _list_lutris_rows()]


def _lutris_config_dirs() -> List[str]:
    home = os.path.expanduser("~")
    return [
        os.path.join(home, ".config", "lutris"),
        os.path.join(home, ".var", "app", LUTRIS_FLATPAK_ID, "config", "lutris"),
    ]


def _load_lutris_game_config(configpath: str) -> Dict[str, object]:
    if not configpath:
        return {}
    candidates = [configpath]
    if not os.path.isabs(configpath):
        candidates += [os.path.join(conf_dir, configpath) for conf_dir in _lutris_config_dirs()]
    for candidate in candidates:
        candidate = os.path.expanduser(candidate)
        if not os.path.isfile(candidate):
            continue
        try:
            with open(candidate, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (OSError, yaml.YAMLError):
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def _section(config: Dict[str, object], key: str) -> Dict[str, object]:
    value = config.get(key)
    return value if isinstance(value, dict) else {}


def _lutris_detect_hints(
    executable: str,
    directory: str,
    configpath: str,
) -> Optional[DetectHints]:
    config = _load_lutris_game_config(configpath)
    game_config = _section(config, "game")
    prefix = str(_section(config, "wine").get("prefix") or "").strip()

    exe = str(game_config.get("exe") or "").strip() or executable.strip()
    if exe:
        exe = os.path.expanduser(exe)
        if not os.path.isabs(exe) and prefix:
            exe = os.path.join(os.path.expanduser(prefix), exe)

    install_dir = directory.strip() or str(game_config.get("working_dir") or "").strip()
    if install_dir:
        install_dir = os.path.expanduser(install_dir)
    elif exe:
        install_dir = os.path.dirname(exe)

    hints: DetectHints = {}
    if exe:
        hints["exe"] = exe
        hints["process_name"] = path_basename_for_hint(exe)
    if install_dir:
        hints["install_dir"] = install_dir
    return normalize_detect_hints(hints)


def list_lutris_selections() -> List[GameSelection]:
    source = LauncherSource("Lutris")
    return [
        GameSelection(
            game_id,
            name,
            "Lutris",
            source,
            _lutris_detect_hints(executable, directory, configpath),
        )
        for game_id, name, executable, directory, configpath in _list_lutris_rows()
    ]
