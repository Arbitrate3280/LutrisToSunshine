import os
import json
import base64
import requests
import getpass
import urllib3
from typing import Tuple, Optional, Dict, List
from config.constants import DEFAULT_IMAGE, CREDENTIALS_PATH
from utils.utils import run_command
from launchers.lutris import get_lutris_command
from launchers.heroic import get_heroic_command

#Remove SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

def get_sunshine_credentials() -> Tuple[str, str]:
    """Retrieves username and password hash from sunshine_state.json."""
    sunshine_state_path = os.path.expanduser("~/.config/sunshine/sunshine_state.json")
    try:
        with open(sunshine_state_path, 'r') as f:
            sunshine_state = json.load(f)
        username = sunshine_state["username"]
        password_hash = sunshine_state["password"]
        return username, password_hash
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
        print(f"Error reading credentials from {sunshine_state_path}: {e}")
        return "", ""

def get_auth_token() -> Optional[str]:
    """Retrieves or generates an authentication token."""
    token_path = os.path.join(CREDENTIALS_PATH, "auth_token.txt")
    if os.path.exists(token_path):
        with open(token_path, 'r') as f:
            return f.read().strip()

    username, password_hash = get_sunshine_credentials()
    if not username or not password_hash:
        return None

    auth_header = f"{username}:{password_hash}"
    encoded_auth = base64.b64encode(auth_header.encode()).decode()
    token = f"Basic {encoded_auth}"

    # Save the token for future use
    os.makedirs(CREDENTIALS_PATH, exist_ok=True)
    with open(token_path, 'w') as f:
        f.write(token)
    return token

def add_game_to_sunshine_api(game_name: str, cmd: str, image_path: str) -> None:
    """Add a game to the Sunshine configuration using the API."""
    token = get_auth_token()
    if not token:
        print("Error: Could not obtain authentication token.")
        return

    headers = {
        "Authorization": token,
        "Content-Type": "application/json"
    }

    payload = {
        "name": game_name,
        "output": "",
        "cmd": cmd,
        "index": -1,
        "exclude-global-prep-cmd": False,
        "elevated": False,
        "auto-detach": True,
        "wait-all": True,
        "exit-timeout": 5,
        "prep-cmd": [],
        "detached": [],
        "image-path": image_path
    }

    cert_path = os.path.expanduser("~/.config/sunshine/credentials/cacert.pem")

    try:
        response = requests.post("https://localhost:47990/api/apps", headers=headers, json=payload, verify=False)
        response.raise_for_status()
        print(f"Added {game_name} to Sunshine using API.")
    except requests.exceptions.RequestException as e:
        print(f"Error adding {game_name} to Sunshine via API: {e}")

def get_sunshine_credentials() -> Tuple[str, str]:
    """Prompts the user for their Sunshine username and password."""
    username = input("Enter your Sunshine username: ")
    password = getpass.getpass("Enter your Sunshine password: ")
    return username, password

def get_auth_token() -> Optional[str]:
    """Retrieves or generates an authentication token."""
    token_path = os.path.join(CREDENTIALS_PATH, "auth_token.txt")
    if os.path.exists(token_path):
        with open(token_path, 'r') as f:
            return f.read().strip()

    username, password = get_sunshine_credentials()
    if not username or not password:
        return None

    # Directly use password instead of password hash
    auth_header = f"{username}:{password}"
    encoded_auth = base64.b64encode(auth_header.encode()).decode()
    token = f"Basic {encoded_auth}"

    # Save the token for future use
    os.makedirs(CREDENTIALS_PATH, exist_ok=True)
    with open(token_path, 'w') as f:
        f.write(token)
    return token

def add_game_to_sunshine(game_id: str, game_name: str, image_path: str, runner: str) -> None:
    """Add a game to the Sunshine configuration."""
    if runner == "Lutris":
        lutris_cmd = get_lutris_command()
        cmd = f"{lutris_cmd} lutris:rungameid/{game_id}"
    elif runner in ["legendary", "gog", "nile", "sideload"]:
        heroic_cmd, _ = get_heroic_command()
        cmd = f"{heroic_cmd} heroic://launch/{runner}/{game_id} --no-gui --no-sandbox"
    else:  # Bottles
        cmd = f'flatpak run --command=bottles-cli com.usebottles.bottles run -b "{runner}" -p "{game_id}"'

    # Use the API instead of directly modifying apps.json
    add_game_to_sunshine_api(game_name, cmd, image_path)

def get_existing_apps() -> List[Dict]:
    """Retrieves the list of existing apps from the Sunshine API."""
    token = get_auth_token()
    if not token:
        print("Error: Could not obtain authentication token.")
        return []

    headers = {
        "Authorization": token
    }

    try:
        response = requests.get("https://localhost:47990/api/apps", headers=headers, verify=False)
        response.raise_for_status()
        data = response.json()

        existing_apps = []
        apps_list = data.get("apps", [])
        if isinstance(apps_list, list):
            for app_data in apps_list:
                if isinstance(app_data, dict) and "name" in app_data:
                    existing_apps.append({"name": app_data["name"]})
        else:
            print("Warning: Unexpected data structure in API response.")

        return existing_apps

    except requests.exceptions.RequestException as e:
        print(f"Error retrieving existing apps from Sunshine API: {e}")
        return []
