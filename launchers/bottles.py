import os
import shlex
from typing import Iterator, List, Optional, Tuple

import yaml

from config.types import DetectHints, GameSelection, LauncherSource
from utils.utils import normalize_detect_hints, path_basename_for_hint, run_command

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

def _iter_gaming_programs() -> Iterator[Tuple[str, dict]]:
    """Yield (bottle, program config) for user-added programs of gaming bottles."""
    for bottle, config in _iter_gaming_bottles():
        programs = config.get("External_Programs") or {}
        for program in programs.values():
            if not isinstance(program, dict) or program.get("removed"):
                continue
            if program.get("name"):
                yield bottle, program


def _bottles_detect_hints(program: dict) -> Optional[DetectHints]:
    hints: DetectHints = {}
    path = str(program.get("path") or "").strip()
    folder = str(program.get("folder") or "").strip()

    if path and os.path.isabs(path):
        hints["exe"] = path
    if path:
        hints["process_name"] = path_basename_for_hint(path)
    if folder and os.path.isabs(folder):
        hints["install_dir"] = folder
    return normalize_detect_hints(hints)


def list_bottles_games() -> List[Tuple[str, str, str, str]]:
    """List programs of gaming bottles by reading bottle.yml directly instead of
    spawning bottles-cli per bottle. Mirrors `bottles-cli programs` semantics for
    user-added programs (External_Programs minus removed entries).
    ponytail: skips .lnk-auto-discovered and Windows Steam/Epic extras that
    bottles-cli also scans; add if a game only shows in Bottles' library."""
    return [
        (program["name"], program["name"], "Bottles", bottle)
        for bottle, program in _iter_gaming_programs()
    ]


def list_bottles_selections() -> List[GameSelection]:
    return [
        GameSelection(
            program["name"],
            program["name"],
            "Bottles",
            LauncherSource(bottle),
            _bottles_detect_hints(program),
        )
        for bottle, program in _iter_gaming_programs()
    ]
