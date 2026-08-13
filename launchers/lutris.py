import os
import shutil
import shlex
from typing import Optional, List, Tuple
from config.types import GameSelection, LauncherSource
from utils.utils import run_command, parse_json_output


def build_lutris_command(game: GameSelection) -> Optional[str]:
    command = get_lutris_command()
    if not command:
        return None
    return shlex.join([*shlex.split(command), f"lutris:rungameid/{game.game_id}"])

def get_lutris_command(args: str = "") -> Optional[str]:
    """Get the appropriate Lutris command based on installation type."""
    # Check for Flatpak installation
    if run_command(["flatpak", "info", "net.lutris.Lutris"]).returncode == 0:
        base_cmd = "flatpak run net.lutris.Lutris"
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

def list_lutris_games() -> List[Tuple[str, str]]:
    """List all games in Lutris."""
    lutris_cmd = get_lutris_command()
    result = run_command([*shlex.split(lutris_cmd or ""), "-lo", "--json"])
    games = parse_json_output(result)
    return [(game['id'], game['name']) for game in games] if games else []


def list_lutris_selections() -> List[GameSelection]:
    source = LauncherSource("Lutris")
    return [GameSelection(game_id, name, "Lutris", source) for game_id, name in list_lutris_games()]
