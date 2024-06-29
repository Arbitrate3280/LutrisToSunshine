import os
import subprocess
import json
import requests
from PIL import Image
from io import BytesIO
from typing import List, Tuple, Dict, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys

# Constants
SUNSHINE_APPS_JSON_PATH = os.path.expanduser("~/.config/sunshine/apps.json")
COVERS_PATH = os.path.expanduser("~/.config/sunshine/covers")
DEFAULT_IMAGE = "default.png"
API_KEY_PATH = os.path.expanduser("~/.config/sunshine/steamgriddb_api_key.txt")

# Ensure the covers directory exists
os.makedirs(COVERS_PATH, exist_ok=True)

def handle_interrupt():
    """Handle script interruption consistently."""
    print("\nScript interrupted by user. Exiting...")
    sys.exit(0)

def run_command(cmd: str) -> subprocess.CompletedProcess:
    """Run a shell command and return the result."""
    return subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def get_lutris_command(command: str) -> str:
    """Get the appropriate Lutris command based on installation type."""
    if run_command("flatpak list | grep net.lutris.Lutris").returncode == 0:
        return f"flatpak run net.lutris.Lutris {command}"
    elif run_command("command -v lutris").returncode == 0:
        return f"lutris {command}"
    else:
        raise Exception("Lutris is not installed.")

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

def get_user_input(prompt: str, validator: callable, error_message: str) -> Any:
    """Get and validate user input."""
    while True:
        try:
            user_input = input(prompt)
            return validator(user_input)
        except ValueError:
            print(error_message)
        except (KeyboardInterrupt, EOFError):
            handle_interrupt()

def yes_no_validator(value: str) -> bool:
    """Validate yes/no input."""
    value = value.strip().lower()
    if value in ['y', 'yes']:
        return True
    elif value in ['n', 'no']:
        return False
    raise ValueError()

def get_yes_no_input(prompt: str) -> bool:
    """Get a yes or no input from the user."""
    return get_user_input(
        prompt,
        yes_no_validator,
        "Invalid input. Please enter 'y' for yes or 'n' for no."
    )

def manage_api_key() -> Optional[str]:
    """Manage the SteamGridDB API key."""
    try:
        if os.path.exists(API_KEY_PATH):
            with open(API_KEY_PATH, 'r') as file:
                return file.read().strip()
        new_key = input("Please enter your SteamGridDB API key: ").strip()
        with open(API_KEY_PATH, 'w') as file:
            file.write(new_key)
        return new_key
    except (KeyboardInterrupt, EOFError):
        handle_interrupt()

def is_lutris_running() -> bool:
    """Check if Lutris is currently running."""
    our_script_name = os.path.basename(__file__)
    cmd = f"ps aux | grep -v grep | grep -v {our_script_name} | grep -E " + r"'(^|\s)lutris($|\s)|net\.lutris\.Lutris'"
    result = run_command(cmd)
    return result.returncode == 0 and result.stdout.strip() != b''

def list_lutris_games() -> List[Tuple[str, str]]:
    """List all games in Lutris."""
    cmd = get_lutris_command("-lo --json")
    result = run_command(cmd)
    games = parse_json_output(result)
    return [(game['id'], game['name']) for game in games] if games else []

def download_image_from_steamgriddb(game_name: str, api_key: str) -> str:
    """Download game cover image from SteamGridDB or return cached image if available."""
    image_path = os.path.join(COVERS_PATH, f"{game_name.lower().replace(' ', '-')}.png")

    if os.path.exists(image_path):
        return image_path

    headers = {"Authorization": f"Bearer {api_key}"}
    search_url = f"https://www.steamgriddb.com/api/v2/search/autocomplete/{game_name}"

    try:
        response = requests.get(search_url, headers=headers)
        response.raise_for_status()
        data = response.json()

        if not data['data']:
            print(f"No results found for {game_name} on SteamGridDB")
            return DEFAULT_IMAGE

        game_id = data['data'][0]['id']
        cover_url = f"https://www.steamgriddb.com/api/v2/grids/game/{game_id}"
        response = requests.get(cover_url, headers=headers)
        response.raise_for_status()
        cover_data = response.json()

        if not cover_data['data']:
            print(f"No cover found for {game_name} on SteamGridDB")
            return DEFAULT_IMAGE

        image_url = cover_data['data'][0]['url']
        image_response = requests.get(image_url)
        image_response.raise_for_status()

        image = Image.open(BytesIO(image_response.content))
        image.save(image_path, "PNG")

        return image_path

    except requests.RequestException as e:
        print(f"Error downloading image for {game_name}: {e}")
        return DEFAULT_IMAGE

def get_user_selection(games: List[Tuple[str, str]]) -> List[int]:
    """Get user selection of games to add."""
    print(f"{len(games) + 1}. Add all games")

    def selection_validator(value: str) -> List[int]:
        if value.strip() == str(len(games) + 1):
            return list(range(len(games)))

        indices = []
        for part in value.split(','):
            part = part.strip()
            if '-' in part:
                start, end = map(int, part.split('-'))
                indices.extend(range(start - 1, end))
            else:
                indices.append(int(part) - 1)

        if all(0 <= i < len(games) for i in indices):
            return list(set(indices))  # Remove duplicates
        raise ValueError()

    return get_user_input(
        "Enter the number(s) of the game(s) you want to add to Sunshine (comma-separated for multiple, or ranges like 2-9): ",
        selection_validator,
        "Invalid selection. Please try again."
    )

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

def add_game_to_sunshine(sunshine_data: Dict, game_id: str, game_name: str, image_path: str) -> None:
    """Add a game to the Sunshine configuration."""
    cmd = get_lutris_command(f"lutris:rungameid/{game_id}")
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

def main():
    try:
        if is_lutris_running():
            print("Error: Lutris is currently running. Please close Lutris and try again.")
            return

        games = list_lutris_games()
        if not games:
            print("No games found in Lutris.")
            return

        sunshine_data = load_sunshine_apps()
        existing_game_names = {app["name"] for app in sunshine_data["apps"]}

        print("Games found in Lutris:")
        for idx, (_, game_name) in enumerate(games):
            status = "(already in Sunshine)" if game_name in existing_game_names else ""
            print(f"{idx + 1}. {game_name} {status}")

        selected_indices = get_user_selection(games)
        selected_games = [games[i] for i in selected_indices if games[i][1] not in existing_game_names]

        if not selected_games:
            print("No new games to add to Sunshine configuration.")
            return

        download_images = get_yes_no_input("Do you want to download images from SteamGridDB? (y/n): ")
        api_key = manage_api_key() if download_images else None

        games_added = False
        with ThreadPoolExecutor() as executor:
            futures = {}
            for game_id, game_name in selected_games:
                if download_images and api_key:
                    future = executor.submit(download_image_from_steamgriddb, game_name, api_key)
                    futures[future] = (game_id, game_name)
                else:
                    add_game_to_sunshine(sunshine_data, game_id, game_name, DEFAULT_IMAGE)
                    games_added = True

            for future in as_completed(futures):
                game_id, game_name = futures[future]
                try:
                    image_path = future.result()
                except Exception as e:
                    print(f"Error downloading image for {game_name}: {e}")
                    image_path = DEFAULT_IMAGE

                add_game_to_sunshine(sunshine_data, game_id, game_name, image_path)
                games_added = True

        if games_added:
            save_sunshine_apps(sunshine_data)
            print("Sunshine configuration updated successfully.")
        else:
            print("No new games were added to Sunshine configuration.")

    except (KeyboardInterrupt, EOFError):
        handle_interrupt()

if __name__ == "__main__":
    main()
