import subprocess
import sys
import json
from typing import Any, Dict, List, Tuple

from config.constants import LAUNCHER_NAMES, SOURCE_PRIORITY
from config.types import GameSelection


def handle_interrupt():
    """Handle script interruption consistently."""
    print("\nScript interrupted by user. Exiting...")
    sys.exit(0)

def run_command(cmd: str) -> subprocess.CompletedProcess:
    """Run a shell command and return the result."""
    return subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def parse_json_output(result: subprocess.CompletedProcess) -> Any:
    """Parse JSON output from a command, handling errors."""
    if result.returncode != 0:
        print(f"Error executing command: {result.stderr.decode()}")
        return None
    try:
        return json.loads(result.stdout.decode())
    except json.JSONDecodeError:
        print("Error parsing JSON output.")
        return None

def parse_bottles_output(result: subprocess.CompletedProcess) -> List[str]:
    """Parse the output of the Bottles list command."""
    if result.returncode != 0:
        print(f"Error executing Bottles command: {result.stderr.decode()}")
        return []
    lines = result.stdout.decode().split('\n')
    return [line.strip('- ') for line in lines if line.startswith('-')]

def parse_bottles_programs(result: subprocess.CompletedProcess) -> List[str]:
    """Parse the output of the Bottles programs command."""
    if result.returncode != 0:
        print(f"Error executing Bottles command: {result.stderr.decode()}")
        return []
    lines = result.stdout.decode().split('\n')
    # Skip the "Found X programs:" line, empty lines, and remove leading "- "
    return [line.strip("- ").strip() for line in lines if line.strip() and not line.startswith("Found")]

def get_games_found_message(detected_launchers: Dict[str, Any]) -> str:
    sources = [name for name in LAUNCHER_NAMES if detected_launchers.get(name)]

    if not sources:
        return "No game sources detected."

    if len(sources) == 1:
        return f"Games found in {sources[0]}:"
    if len(sources) == 2:
        return f"Games found in {sources[0]} and {sources[1]}:"
    return f"Games found in {', '.join(sources[:-1])} and {sources[-1]}:"


def normalize_game_name_for_dedup(game_name: str) -> str:
    """Normalize a display name for duplicate detection."""
    return " ".join(game_name.casefold().split())


def get_source_priority(display_source: str) -> int:
    """Return the deduplication priority for a display source.

    Lower numbers win. Unknown sources are treated as lowest priority so
    the well-known launchers always take precedence.
    """
    return SOURCE_PRIORITY.get(display_source, len(SOURCE_PRIORITY))


def dedupe_selected_games_by_name(
    games: List[GameSelection],
) -> Tuple[List[GameSelection], List[Tuple[GameSelection, GameSelection]]]:
    """Collapse selected games that share a normalized display name.

    For each normalized name group, the entry with the lowest source
    priority wins; ties keep the first selected entry. Output preserves
    stable order by the first occurrence of each normalized group in the
    input, but the entry shown there is the higher-priority winner when
    a later duplicate replaces an earlier retained game.

    Returns a tuple of ``(deduped_games, skipped_duplicates)`` where
    ``skipped_duplicates`` is a list of ``(skipped_game, retained_game)``
    pairs ready to be surfaced as user-facing messages.
    """
    retained_by_name: Dict[str, GameSelection] = {}
    order: List[str] = []
    skipped: List[Tuple[GameSelection, GameSelection]] = []

    for game in games:
        _, game_name, display_source, _ = game
        key = normalize_game_name_for_dedup(game_name)
        if key not in retained_by_name:
            retained_by_name[key] = game
            order.append(key)
            continue

        retained = retained_by_name[key]
        _, _, retained_source, _ = retained
        if get_source_priority(display_source) < get_source_priority(retained_source):
            skipped.append((retained, game))
            retained_by_name[key] = game
        else:
            skipped.append((game, retained))

    return [retained_by_name[key] for key in order], skipped
