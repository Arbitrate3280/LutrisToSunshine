import os
import json
from typing import Tuple, Optional, Dict
from config.constants import SUNSHINE_APPS_JSON_PATH, DEFAULT_IMAGE
from utils.utils import run_command
from launchers.lutris import get_lutris_command
from launchers.heroic import get_heroic_command

def detect_sunshine_installation() -> Tuple[bool, str]:
    """Detect if Sunshine is installed and how."""
    # Check for Flatpak installation
    if run_command("flatpak list | grep dev.lizardbyte.app.Sunshine").returncode == 0:
        return True, "flatpak"
    # Check for native installation
    elif run_command("which sunshine").returncode == 0:
        return True, "native"
    else:
        return False, ""

def load_sunshine_apps() -> Dict:
    """Load Sunshine apps configuration."""
    if not os.path.exists(SUNSHINE_APPS_JSON_PATH):
        return {"env": {"PATH": "$(PATH):$(HOME)/.local/bin"}, "apps": []}

    try:
        with open(SUNSHINE_APPS_JSON_PATH, 'r') as file:
            return json.load(file)
    except json.JSONDecodeError:
        print("Error parsing Sunshine apps JSON. Using default configuration.")
        return {"env": {"PATH": "$(PATH):$(HOME)/.local/bin"}, "apps": []}

def save_sunshine_apps(data: Dict) -> None:
    """Save Sunshine apps configuration."""
    with open(SUNSHINE_APPS_JSON_PATH, 'w') as file:
        json.dump(data, file, indent=4)

def add_game_to_sunshine(sunshine_data: Dict, game_id: str, game_name: str, image_path: str, runner: str) -> None:
    """Add a game to the Sunshine configuration."""
    if runner == "Lutris":
        lutris_cmd = get_lutris_command()
        cmd = f"{lutris_cmd} lutris:rungameid/{game_id}"
    elif runner in ["legendary", "gog", "nile", "sideload"]:
        heroic_cmd, _ = get_heroic_command()
        cmd = f"{heroic_cmd} heroic://launch/{runner}/{game_id} --no-gui --no-sandbox"
    else:  # Bottles
        cmd = f'flatpak run --command=bottles-cli com.usebottles.bottles run -b "{runner}" -p "{game_id}"'

    new_app = {
        "name": game_name,
        "cmd": cmd,
        "image-path": image_path,
        "auto-detach": "true",
        "wait-all": "true",
        "exit-timeout": "5"
    }
    sunshine_data["apps"].append(new_app)
    print(f"Added {game_name} to Sunshine with image {image_path}.")
