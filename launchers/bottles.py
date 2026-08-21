import os
import shlex
from typing import Iterator, List, Tuple

import yaml

from config.types import GameSelection, LauncherSource
from utils.utils import run_command

BOTTLES_FLATPAK_ID = "com.usebottles.bottles"


def build_bottles_command(game: GameSelection) -> str:
    return shlex.join(
        [
            "flatpak",
            "run",
            "--command=bottles-cli",
            BOTTLES_FLATPAK_ID,
            "run",
            "-b",
            str(game.source),
            "-p",
            game.game_id,
        ]
    )

def detect_bottles_installation() -> bool:
    """Detect if Bottles is installed via Flatpak."""
    return run_command(["flatpak", "info", BOTTLES_FLATPAK_ID]).returncode == 0

def _bottles_data_roots() -> List[str]:
    """Bottles data roots (Paths.base equivalents), flatpak first."""
    home = os.path.expanduser("~")
    return [
        os.path.join(home, ".var", "app", BOTTLES_FLATPAK_ID, "data", "bottles"),
        os.path.join(home, ".local", "share", "bottles"),
    ]

def _iter_gaming_bottles() -> Iterator[Tuple[str, dict]]:
    """Yield (name, config) for gaming bottles read from their bottle.yml files —
    the same configs bottles-cli reads. First root wins on name clashes."""
    seen = set()
    for root in _bottles_data_roots():
        bottles_dir = os.path.join(root, "bottles")
        if not os.path.isdir(bottles_dir):
            continue
        for name in sorted(os.listdir(bottles_dir)):
            if name in seen:
                continue
            config_path = os.path.join(bottles_dir, name, "bottle.yml")
            if not os.path.isfile(config_path):
                continue
            try:
                with open(config_path, encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
            except (OSError, yaml.YAMLError):
                continue
            if str(config.get("Environment", "")).lower() != "gaming":
                continue
            seen.add(name)
            yield name, config

def list_bottles_games() -> List[Tuple[str, str, str, str]]:
    """List programs of gaming bottles by reading bottle.yml directly instead of
    spawning bottles-cli per bottle. Mirrors `bottles-cli programs` semantics for
    user-added programs (External_Programs minus removed entries).
    ponytail: skips .lnk-auto-discovered and Windows Steam/Epic extras that
    bottles-cli also scans; add if a game only shows in Bottles' library."""
    games = []
    for bottle, config in _iter_gaming_bottles():
        programs = config.get("External_Programs") or {}
        for program in programs.values():
            if not isinstance(program, dict) or program.get("removed"):
                continue
            program_name = program.get("name")
            if program_name:
                games.append((program_name, program_name, "Bottles", bottle))
    return games


def list_bottles_selections() -> List[GameSelection]:
    return [
        GameSelection(game_id, name, "Bottles", LauncherSource(bottle))
        for game_id, name, _, bottle in list_bottles_games()
    ]
