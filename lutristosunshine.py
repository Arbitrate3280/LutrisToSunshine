import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from config.constants import COVERS_PATH, DEFAULT_IMAGE, SOURCE_COLORS, RESET_COLOR
from utils.utils import handle_interrupt, run_command, get_games_found_message, parse_json_output
from utils.input import get_yes_no_input, get_user_selection
from sunshine.sunshine import detect_sunshine_installation, add_game_to_sunshine, get_existing_apps, get_auth_token
from utils.steamgriddb import manage_api_key, download_image_from_steamgriddb
from launchers.heroic import list_heroic_games, get_heroic_command, HEROIC_PATHS
from launchers.lutris import list_lutris_games, get_lutris_command, is_lutris_running
from launchers.bottles import detect_bottles_installation, list_bottles_games

# Ensure the covers directory exists
os.makedirs(COVERS_PATH, exist_ok=True)

def main():
    try:
        sunshine_installed, installation_type = detect_sunshine_installation()
        if not sunshine_installed:
            print("Error: No Sunshine installation detected.")
            return
        if installation_type == "flatpak":
            print("Error: Sunshine Flatpak is not supported. Please use the native installation of Sunshine.")
            return

        token = None
        while token is None:
            token = get_auth_token()

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

        existing_apps = get_existing_apps()
        existing_game_names = {app["name"] for app in existing_apps}

        # Sort the games alphabetically by name
        all_games.sort(key=lambda x: x[1])

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
                    add_game_to_sunshine(game_id, game_name, DEFAULT_IMAGE, source)
                    games_added = True

            for future in as_completed(futures):
                game_id, game_name, source = futures[future]
                try:
                    image_path = future.result()
                except Exception as e:
                    print(f"Error downloading image for {game_name}: {e}")
                    image_path = DEFAULT_IMAGE

                add_game_to_sunshine(game_id, game_name, image_path, source)
                games_added = True

        if games_added:
            print("Games added to Sunshine successfully.")
        else:
            print("No new games were added to Sunshine.")

    except (KeyboardInterrupt, EOFError):
        handle_interrupt()

if __name__ == "__main__":
    main()