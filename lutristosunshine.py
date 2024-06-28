import os
import subprocess
import json
import requests
from PIL import Image
from io import BytesIO
from typing import List, Tuple, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import keyring

# Constants
SUNSHINE_APPS_JSON_PATH = os.path.expanduser("~/.config/sunshine/apps.json")
COVERS_PATH = os.path.expanduser("~/.config/sunshine/covers")
DEFAULT_IMAGE = "default.png"
SERVICE_NAME = "LutrisToSunshine"
USERNAME = "SteamGridDB"

# Ensure the covers directory exists
os.makedirs(COVERS_PATH, exist_ok=True)

def is_flatpak_installed() -> bool:
    """Check if Lutris is installed via Flatpak."""
    cmd = "flatpak list | grep net.lutris.Lutris"
    result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.returncode == 0

def is_lutris_installed() -> bool:
    """Check if Lutris is installed traditionally."""
    cmd = "command -v lutris"
    result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.returncode == 0

def is_lutris_running() -> bool:
    """Check if Lutris is currently running."""
    our_script_name = os.path.basename(__file__)
    cmd = f"ps aux | grep -v grep | grep -v {our_script_name} | grep -E " + r"'(^|\s)lutris($|\s)|net\.lutris\.Lutris'"
    result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.returncode == 0 and result.stdout.strip() != b''

def list_lutris_games() -> List[Tuple[str, str]]:
    """List all games in Lutris."""
    if is_flatpak_installed():
        cmd = "flatpak run net.lutris.Lutris -lo --json"
    elif is_lutris_installed():
        cmd = "lutris -lo --json"
    else:
        print("Lutris is not installed.")
        return []

    result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print("Error listing Lutris games:")
        print(result.stderr.decode())
        return []

    try:
        games = json.loads(result.stdout.decode())
        return [(game['id'], game['name']) for game in games]
    except json.JSONDecodeError:
        print("Error parsing Lutris games JSON.")
        return []

def download_image_from_steamgriddb(game_name: str, api_key: str) -> str:
    """Download game cover image from SteamGridDB or return cached image if available."""
    image_path = os.path.join(COVERS_PATH, f"{game_name.lower().replace(' ', '-')}.png")

    # Check if image is already cached
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

def load_api_key() -> Optional[str]:
    """Load SteamGridDB API key from the system keyring."""
    return keyring.get_password(SERVICE_NAME, USERNAME)

def save_api_key(api_key: str) -> None:
    """Save SteamGridDB API key to the system keyring."""
    keyring.set_password(SERVICE_NAME, USERNAME, api_key)

def get_user_selection(games: List[Tuple[str, str]]) -> List[int]:
    """Get user selection of games to add."""
    print(f"{len(games) + 1}. Add all games")
    while True:
        selection = input("Enter the number(s) of the game(s) you want to add to Sunshine (comma-separated for multiple, or ranges like 2-9): ")
        if selection.strip() == str(len(games) + 1):
            return list(range(len(games)))
        try:
            indices = []
            for part in selection.split(','):
                part = part.strip()
                if '-' in part:
                    start, end = map(int, part.split('-'))
                    indices.extend(range(start - 1, end))
                else:
                    indices.append(int(part) - 1)

            if all(0 <= i < len(games) for i in indices):
                return list(set(indices))  # Remove duplicates
            print("Invalid selection. Please try again.")
        except ValueError:
            print("Invalid input. Please enter numbers or ranges separated by commas.")

def get_yes_no_input(prompt: str) -> bool:
    """Get a yes or no input from the user."""
    while True:
        response = input(prompt).strip().lower()
        if response in ['y', 'yes']:
            return True
        elif response in ['n', 'no']:
            return False
        else:
            print("Invalid input. Please enter 'y' for yes or 'n' for no.")

def main():
    try:
        if is_lutris_running():
            print("Error: Lutris is currently running. Please close Lutris and try again.")
            return

        download_images = get_yes_no_input("Do you want to download images from SteamGridDB? (y/n): ")
        api_key = None
        if download_images:
            api_key = load_api_key()
            if not api_key:
                api_key = input("Please enter your SteamGridDB API key: ").strip()
                save_api_key(api_key)
            else:
                use_existing = get_yes_no_input("An existing API key was found. Do you want to use it? (y/n): ")
                if not use_existing:
                    api_key = input("Please enter your new SteamGridDB API key: ").strip()
                    save_api_key(api_key)

        games = list_lutris_games()
        if not games:
            return

        sunshine_data = load_sunshine_apps()
        existing_game_names = {app["name"] for app in sunshine_data["apps"]}

        print("Games found in Lutris:")
        for idx, (_, game_name) in enumerate(games):
            status = "(already in Sunshine)" if game_name in existing_game_names else ""
            print(f"{idx + 1}. {game_name} {status}")

        selected_indices = get_user_selection(games)
        selected_games = [games[i] for i in selected_indices]

        games_added = False
        with ThreadPoolExecutor() as executor:
            futures = {executor.submit(download_image_from_steamgriddb, game_name, api_key): game_name for _, game_name in selected_games if download_images and api_key}

            for future in as_completed(futures):
                game_name = futures[future]
                try:
                    image_path = future.result()
                except Exception as e:
                    print(f"Error downloading image for {game_name}: {e}")
                    image_path = DEFAULT_IMAGE

                for game_id, name in selected_games:
                    if name == game_name and name not in existing_game_names:
                        cmd = f"env LUTRIS_SKIP_INIT=1 {'flatpak run net.lutris.Lutris' if is_flatpak_installed() else 'lutris'} lutris:rungameid/{game_id}"

                        new_app = {
                            "name": name,
                            "cmd": cmd,
                            "image-path": image_path,
                            "auto-detach": "true",
                            "wait-all": "true",
                            "exit-timeout": "5"
                        }
                        sunshine_data["apps"].append(new_app)
                        print(f"Added {name} to Sunshine with image {image_path}.")
                        games_added = True

        if games_added:
            save_sunshine_apps(sunshine_data)
            print("Sunshine configuration updated successfully.")
        else:
            print("No new games were added to Sunshine configuration.")

    except KeyboardInterrupt:
        print("\nScript interrupted by user. Exiting...")

if __name__ == "__main__":
    main()
