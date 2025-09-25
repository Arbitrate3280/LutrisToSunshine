from typing import List, Tuple
import os
from utils.utils import run_command, parse_bottles_output, parse_bottles_programs

def detect_bottles_installation() -> bool:
    """Detect if Bottles is installed via Flatpak."""
    return run_command("flatpak list | grep com.usebottles.bottles").returncode == 0

def list_bottles_games() -> List[Tuple[str, str, str, str]]:
    """List all games in Bottles."""
    games = []
    bottles_dir = os.path.expanduser("~/.var/app/com.usebottles.bottles/data/bottles/bottles")
    if not os.path.exists(bottles_dir):
        return []
    cmd = "flatpak run --command=bottles-cli com.usebottles.bottles list bottles -f environment:gaming"
    result = run_command(cmd)
    bottles = parse_bottles_output(result)

    for bottle in bottles:
        cmd = f'flatpak run --command=bottles-cli com.usebottles.bottles programs -b "{bottle}"'
        result = run_command(cmd)
        programs = parse_bottles_programs(result)
        for program in programs:
            games.append((program, program, "Bottles", bottle))

    return games
