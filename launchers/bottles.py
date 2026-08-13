import shlex
from typing import List, Tuple
from config.types import GameSelection, LauncherSource
import os
from utils.utils import run_command, parse_bottles_output, parse_bottles_programs


def build_bottles_command(game: GameSelection) -> str:
    return shlex.join(
        [
            "flatpak",
            "run",
            "--command=bottles-cli",
            "com.usebottles.bottles",
            "run",
            "-b",
            str(game.source),
            "-p",
            game.game_id,
        ]
    )

def detect_bottles_installation() -> bool:
    """Detect if Bottles is installed via Flatpak."""
    return run_command(["flatpak", "info", "com.usebottles.bottles"]).returncode == 0

def list_bottles_games() -> List[Tuple[str, str, str, str]]:
    """List all games in Bottles."""
    games = []
    bottles_dir = os.path.expanduser("~/.var/app/com.usebottles.bottles/data/bottles/bottles")
    if not os.path.exists(bottles_dir):
        return []
    base_cmd = ["flatpak", "run", "--command=bottles-cli", "com.usebottles.bottles"]
    result = run_command([*base_cmd, "list", "bottles", "-f", "environment:gaming"])
    bottles = parse_bottles_output(result)

    for bottle in bottles:
        result = run_command([*base_cmd, "programs", "-b", bottle])
        programs = parse_bottles_programs(result)
        for program in programs:
            games.append((program, program, "Bottles", bottle))

    return games


def list_bottles_selections() -> List[GameSelection]:
    return [
        GameSelection(game_id, name, "Bottles", LauncherSource(bottle))
        for game_id, name, _, bottle in list_bottles_games()
    ]
