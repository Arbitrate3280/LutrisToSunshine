import os
import requests
from PIL import Image
from io import BytesIO
from typing import Optional
from config.constants import API_KEY_PATH, COVERS_PATH, DEFAULT_IMAGE
from utils.utils import handle_interrupt

def validate_api_key(api_key: str) -> bool:
    """Validate the SteamGridDB API key."""
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        response = requests.get("https://www.steamgriddb.com/api/v2/grids/game/1", headers=headers)
        return response.status_code == 200
    except requests.RequestException:
        return False

def manage_api_key() -> Optional[str]:
    """Manage the SteamGridDB API key."""
    try:
        if os.path.exists(API_KEY_PATH):
            with open(API_KEY_PATH, 'r') as file:
                api_key = file.read().strip()
                if validate_api_key(api_key):
                    return api_key
                else:
                    print("Existing API key is invalid. Please enter a new one.")

        print("To get your SteamGridDB API key, visit: https://www.steamgriddb.com/profile/preferences/api")
        while True:
            new_key = input("Please enter your SteamGridDB API key: ").strip()
            if validate_api_key(new_key):
                with open(API_KEY_PATH, 'w') as file:
                    file.write(new_key)
                return new_key
            else:
                print("Invalid API key. Please try again.")
    except (KeyboardInterrupt, EOFError):
        handle_interrupt()

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
        cover_url = f"https://www.steamgriddb.com/api/v2/grids/game/{game_id}?dimensions=600x900&types=static"
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
        image = image.convert("P", palette=Image.ADAPTIVE, colors=256)
        image.save(image_path, "PNG", optimize=True)

        return image_path

    except requests.RequestException as e:
        print(f"Error downloading image for {game_name}: {e}")
        return DEFAULT_IMAGE
