import subprocess
import sys
import json
from typing import Any, List

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

def get_games_found_message(lutris_command, heroic_command, bottles_installed, steam_command):
    sources = set()
    if lutris_command:
        sources.add("Lutris")
    if heroic_command:
        sources.add("Heroic")
    if bottles_installed:
        sources.add("Bottles")
    if steam_command:
        sources.add("Steam")

    if not sources:
        return "No game sources detected."

    sources_list = list(sources)
    if len(sources) == 1:
        return f"Games found in {sources_list[0]}:"
    elif len(sources) == 2:
        return f"Games found in {sources_list[0]} and {sources_list[1]}:"
    else:
        return f"Games found in {', '.join(sources_list[:-1])} and {sources_list[-1]}:"
