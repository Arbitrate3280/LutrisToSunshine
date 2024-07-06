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
HEROIC_PATHS = {
    "flatpak": {
        "legendary": os.path.expanduser("~/.var/app/com.heroicgameslauncher.hgl/config/heroic/legendaryConfig/legendary/installed.json"),
        "gog": os.path.expanduser("~/.var/app/com.heroicgameslauncher.hgl/config/heroic/gog_store/installed.json"),
        "nile": os.path.expanduser("~/.var/app/com.heroicgameslauncher.hgl/config/heroic/nile_config/nile/installed.json"),
        "sideload": os.path.expanduser("~/.var/app/com.heroicgameslauncher.hgl/config/heroic/sideload_apps/library.json")
    },
    "native": {
        "legendary": os.path.expanduser("~/.config/heroic/legendaryConfig/legendary/installed.json"),
        "gog": os.path.expanduser("~/.config/heroic/gog_store/installed.json"),
        "nile": os.path.expanduser("~/.config/heroic/nile_config/nile/installed.json"),
        "sideload": os.path.expanduser("~/.config/heroic/sideload_apps/library.json")
    }
}

# Ensure the covers directory exists
os.makedirs(COVERS_PATH, exist_ok=True)

def handle_interrupt():
    """Handle script interruption consistently."""
    print("\nScript interrupted by user. Exiting...")
    sys.exit(0)

def run_command(cmd: str) -> subprocess.CompletedProcess:
    """Run a shell command and return the result."""
    return subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

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

def get_lutris_command(args: str = "") -> Optional[str]:
    """Get the appropriate Lutris command based on installation type."""
    # Check for Flatpak installation
    if run_command("flatpak list | grep net.lutris.Lutris").returncode == 0:
        base_cmd = "flatpak run net.lutris.Lutris"
    # Check for native installation
    elif run_command("which lutris").returncode == 0:
        base_cmd = "lutris"
    else:
        return None

    return f"{base_cmd} {args}".strip()

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

def get_games_found_message(lutris_command, heroic_command, bottles_installed):
    sources = set()
    if lutris_command:
        sources.add("Lutris")
    if heroic_command:
        sources.add("Heroic")
    if bottles_installed:
        sources.add("Bottles")

    if not sources:
        return "No game sources detected."

    sources_list = list(sources)
    if len(sources) == 1:
        return f"Games found in {sources_list[0]}:"
    elif len(sources) == 2:
        return f"Games found in {sources_list[0]} and {sources_list[1]}:"
    else:
        return f"Games found in {', '.join(sources_list[:-1])} and {sources_list[-1]}:"

def is_lutris_running() -> bool:
    """Check if Lutris is currently running."""
    our_script_name = os.path.basename(__file__)
    cmd = f"ps aux | grep -v grep | grep -v {our_script_name} | grep -E " + r"'(^|\s)lutris($|\s)|net\.lutris\.Lutris'"
    result = run_command(cmd)
    return result.returncode == 0 and result.stdout.strip() != b''

def list_lutris_games() -> List[Tuple[str, str]]:
    """List all games in Lutris."""
    lutris_cmd = get_lutris_command()
    cmd = f"{lutris_cmd} -lo --json"
    result = run_command(cmd)
    games = parse_json_output(result)
    return [(game['id'], game['name']) for game in games] if games else []

def list_heroic_games() -> List[Tuple[str, str, str, str]]:
    """List all games in Heroic."""
    games = []
    heroic_cmd, installation_type = get_heroic_command()

    if not heroic_cmd or not installation_type:
        return games

    for runner, path in HEROIC_PATHS[installation_type].items():
        if os.path.exists(path):
            try:
                with open(path, 'r') as file:
                    data = json.load(file)
                    if isinstance(data, dict):
                        if "installed" in data:
                            # Handling GOG games
                            for game in data["installed"]:
                                if isinstance(game, dict):
                                    app_id = game.get("appName") or game.get("app_name")
                                    install_path = game.get("install_path")
                                    if install_path:
                                        title = install_path.split('/')[-1]
                                    else:
                                        title = app_id
                                    if app_id and title:
                                        games.append((app_id, title, "Heroic", runner))
                        elif "games" in data:
                            # Handling sideloaded games
                            for game in data["games"]:
                                if isinstance(game, dict):
                                    app_id = game.get("app_name")
                                    title = game.get("title")
                                    if app_id and title:
                                        games.append((app_id, title, "Heroic", "sideload"))
                        else:
                            # Handling Legendary games
                            for app_id, game in data.items():
                                if isinstance(game, dict):
                                    title = game.get("title") or game.get("app_name")
                                    if app_id and title:
                                        games.append((app_id, title, "Heroic", runner))
                    elif isinstance(data, list):
                        # In case there are other list-based structures in future
                        for game in data:
                            if isinstance(game, dict):
                                app_id = game.get("appName") or game.get("app_name")
                                install_path = game.get("install_path")
                                if install_path:
                                    title = install_path.split('/')[-1]
                                else:
                                    title = app_id
                                if app_id and title:
                                    games.append((app_id, title, "Heroic", runner))
            except json.JSONDecodeError:
                print(f"Error parsing JSON file at {path}")
    return games

def get_runner_type(path: str) -> str:
    """Map the runner to its correct type based on the path."""
    if "legendaryConfig" in path:
        return "legendary"
    elif "gog_store" in path:
        return "gog"
    elif "nile_config" in path:
        return "nile"
    return "heroic"

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

        # Check if the image is already a PNG
        content_type = image_response.headers.get('Content-Type', '')
        if content_type.lower() == 'image/png':
            # If it's already a PNG, save it directly
            with open(image_path, 'wb') as f:
                f.write(image_response.content)
        else:
            # If it's not a PNG, open it with PIL and save as PNG
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

def get_heroic_command() -> Tuple[Optional[str], Optional[str]]:
    """Get the appropriate Heroic command based on installation type."""
    # Check for Flatpak installation
    if run_command("flatpak list | grep com.heroicgameslauncher.hgl").returncode == 0:
        return "flatpak run com.heroicgameslauncher.hgl", "flatpak"
    # Check for native installation
    elif run_command("which heroic").returncode == 0:
        return "heroic", "native"
    else:
        return None, None

    return f"{base_cmd} {args}".strip(), installation_type

def detect_bottles_installation() -> bool:
    """Detect if Bottles is installed via Flatpak."""
    return run_command("flatpak list | grep com.usebottles.bottles").returncode == 0

def list_bottles_games() -> List[Tuple[str, str, str, str]]:
    """List all games in Bottles."""
    games = []
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

def main():
    try:
        sunshine_installed, installation_type = detect_sunshine_installation()
        if not sunshine_installed:
            print("Error: No Sunshine installation detected.")
            return
        if installation_type == "flatpak":
            print("Warning: Sunshine Flatpak is not supported. Please use the native installation of Sunshine.")
            return

        lutris_command = get_lutris_command()
        heroic_command, _ = get_heroic_command()
        bottles_installed = detect_bottles_installation()

        if not lutris_command and not heroic_command and not bottles_installed:
            print("No Lutris, Heroic, or Bottles installation detected.")
            return

        if lutris_command and is_lutris_running():
            print("Error: Lutris is currently running. Please close Lutris and try again.")
            return

        with ThreadPoolExecutor() as executor:
            futures = {}
            if lutris_command:
                futures['Lutris'] = executor.submit(list_lutris_games)
            if heroic_command:
                futures['Heroic'] = executor.submit(list_heroic_games)
            if bottles_installed:
                futures['Bottles'] = executor.submit(list_bottles_games)

            all_games = []
            for source, future in futures.items():
                result = future.result()
                if source == 'Lutris':
                    all_games.extend([(game_id, game_name, "Lutris", "Lutris") for game_id, game_name in result])
                elif source == 'Heroic':
                    all_games.extend([(game_id, game_name, "Heroic", runner) for game_id, game_name, _, runner in result])
                elif source == 'Bottles':
                    all_games.extend(result)  # Bottles results are already in the correct format

        if not all_games:
            print("No games found in Lutris, Heroic, or Bottles.")
            return

        games_found_message = get_games_found_message(lutris_command, heroic_command, bottles_installed)
        print(games_found_message)
        sunshine_data = load_sunshine_apps()
        existing_game_names = {app["name"] for app in sunshine_data["apps"]}

        # Sort the games alphabetically by name
        all_games.sort(key=lambda x: x[1])

        SOURCE_COLORS = {
            "Heroic": "\033[38;5;39m",  # #3CA6F9
            "Lutris": "\033[38;5;214m",  # #FFAF00
            "Bottles": "\033[38;5;203m"  # #F3544B
        }
        RESET_COLOR = "\033[0m"

        for idx, (_, game_name, display_source, source) in enumerate(all_games):
            status = "(already in Sunshine)" if game_name in existing_game_names else ""
            if len(futures) > 1:  # Only show colors if there's more than one source
                source_color = SOURCE_COLORS.get(display_source, "")
                source_info = f"{source_color}({display_source}){RESET_COLOR}"
                print(f"{idx + 1}. {game_name} {source_info} {status}")
            else:
                print(f"{idx + 1}. {game_name} {status}")

        selected_indices = get_user_selection([(game_id, game_name) for game_id, game_name, _, _ in all_games])
        selected_games = [all_games[i] for i in selected_indices if all_games[i][1] not in existing_game_names]

        if not selected_games:
            print("No new games to add to Sunshine configuration.")
            return

        download_images = get_yes_no_input("Do you want to download images from SteamGridDB? (y/n): ")
        api_key = manage_api_key() if download_images else None

        games_added = False
        with ThreadPoolExecutor() as executor:
            futures = {}
            for game_id, game_name, display_source, source in selected_games:
                if download_images and api_key:
                    future = executor.submit(download_image_from_steamgriddb, game_name, api_key)
                    futures[future] = (game_id, game_name, source)
                else:
                    add_game_to_sunshine(sunshine_data, game_id, game_name, DEFAULT_IMAGE, source)
                    games_added = True

            for future in as_completed(futures):
                game_id, game_name, source = futures[future]
                try:
                    image_path = future.result()
                except Exception as e:
                    print(f"Error downloading image for {game_name}: {e}")
                    image_path = DEFAULT_IMAGE

                add_game_to_sunshine(sunshine_data, game_id, game_name, image_path, source)
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
