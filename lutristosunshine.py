import os
import subprocess
import json
import requests
from PIL import Image
from io import BytesIO

# Define default paths
SUNSHINE_APPS_JSON_PATH = os.path.expanduser("~/.config/sunshine/apps.json")
COVERS_PATH = os.path.expanduser("~/.config/sunshine/covers")
API_KEY_PATH = os.path.expanduser("~/.config/sunshine/steamgriddb_api_key.txt")

# Ensure the covers directory exists
os.makedirs(COVERS_PATH, exist_ok=True)

def is_flatpak_installed():
    cmd = "flatpak list | grep net.lutris.Lutris"
    result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.returncode == 0

def is_lutris_installed():
    cmd = "command -v lutris"
    result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.returncode == 0

def is_lutris_running():
    # Get the name of our script
    our_script_name = os.path.basename(__file__)

    # Construct a command that excludes our script and grep from the search
    cmd = f"ps aux | grep -v grep | grep -v {our_script_name} | grep -E " + r"'(^|\s)lutris($|\s)|net\.lutris\.Lutris'"

    result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.returncode == 0 and result.stdout.strip() != b''

def list_lutris_games():
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

    games_json = result.stdout.decode()
    games = json.loads(games_json)
    game_names = [(game['id'], game['name']) for game in games]
    return game_names

def download_image_from_steamgriddb(game_name, api_key):
    headers = {
        "Authorization": f"Bearer {api_key}",
    }
    search_url = f"https://www.steamgriddb.com/api/v2/search/autocomplete/{game_name}"
    response = requests.get(search_url, headers=headers)

    if response.status_code != 200:
        print(f"Error searching for {game_name} on SteamGridDB")
        return "default.png"

    data = response.json()
    if not data['data']:
        print(f"No results found for {game_name} on SteamGridDB")
        return "default.png"

    game_id = data['data'][0]['id']
    cover_url = f"https://www.steamgriddb.com/api/v2/grids/game/{game_id}"
    response = requests.get(cover_url, headers=headers)

    if response.status_code != 200:
        print(f"Error getting cover for {game_name} on SteamGridDB")
        return "default.png"

    cover_data = response.json()
    if not cover_data['data']:
        print(f"No cover found for {game_name} on SteamGridDB")
        return "default.png"

    image_url = cover_data['data'][0]['url']
    image_response = requests.get(image_url)

    if image_response.status_code != 200:
        print(f"Error downloading image for {game_name} from SteamGridDB")
        return "default.png"

    # Convert image to PNG format
    image = Image.open(BytesIO(image_response.content))
    image_path = os.path.join(COVERS_PATH, f"{game_name.lower().replace(' ', '-')}.png")
    image.save(image_path, "PNG")

    return image_path

def load_sunshine_apps():
    if not os.path.exists(SUNSHINE_APPS_JSON_PATH):
        return {"env": {"PATH": "$(PATH):$(HOME)/.local/bin"}, "apps": []}

    with open(SUNSHINE_APPS_JSON_PATH, 'r') as file:
        data = json.load(file)
    return data

def save_sunshine_apps(data):
    with open(SUNSHINE_APPS_JSON_PATH, 'w') as file:
        json.dump(data, file, indent=4)

def load_api_key():
    if os.path.exists(API_KEY_PATH):
        with open(API_KEY_PATH, 'r') as file:
            return file.read().strip()
    return None

def save_api_key(api_key):
    with open(API_KEY_PATH, 'w') as file:
        file.write(api_key)

def main():
    try:
        if is_lutris_running():
            print("Error: Lutris is currently running. Please close Lutris and try again.")
            return

        download_images = input("Do you want to download images from SteamGridDB? (y/n): ").strip().lower()
        api_key = None
        if download_images == 'y':
            api_key = load_api_key()
            if not api_key:
                api_key = input("Please enter your SteamGridDB API key: ").strip()
                save_api_key(api_key)

        games = list_lutris_games()
        if not games:
            return

        sunshine_data = load_sunshine_apps()
        existing_game_names = [app["name"] for app in sunshine_data["apps"]]

        print("Games found in Lutris:")
        for idx, (_, game_name) in enumerate(games):
            status = "(already in Sunshine)" if game_name in existing_game_names else ""
            print(f"{idx + 1}. {game_name} {status}")

        print(f"{len(games) + 1}. Add all games")

        selection = input("Enter the number of the game you want to add to Sunshine (comma-separated for multiple, or add all): ")
        if selection.strip() == str(len(games) + 1):
            selected_indices = list(range(len(games)))
        else:
            selected_indices = [int(i.strip()) - 1 for i in selection.split(",") if i.strip().isdigit()]

        selected_games = [games[i] for i in selected_indices if 0 <= i < len(games)]

        for game_id, game_name in selected_games:
            if game_name in existing_game_names:
                print(f"{game_name} is already in Sunshine. Skipping.")
                continue

            if download_images == 'y' and api_key:
                image_path = download_image_from_steamgriddb(game_name, api_key)
            else:
                image_path = "default.png"

            if is_flatpak_installed():
                cmd = f"env LUTRIS_SKIP_INIT=1 flatpak run net.lutris.Lutris lutris:rungameid/{game_id}"
            else:
                cmd = f"env LUTRIS_SKIP_INIT=1 lutris lutris:rungameid/{game_id}"

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

        save_sunshine_apps(sunshine_data)
    except KeyboardInterrupt:
        print("\nScript interrupted by user. Exiting...")
        return

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nScript interrupted by user. Exiting...")
