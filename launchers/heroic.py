import os
import json
from typing import Optional, List, Tuple
from utils.utils import run_command

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
