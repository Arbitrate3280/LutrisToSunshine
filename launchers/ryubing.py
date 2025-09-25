import json
import os
import re
from typing import List, Tuple

def detect_ryubing_installation() -> bool:
    """Detect if Ryubing is installed via Flatpak."""
    from utils.utils import run_command
    return run_command("flatpak list | grep io.github.ryubing.Ryujinx").returncode == 0

def get_ryubing_config_path() -> str:
    """Get the Ryubing config file path."""
    return os.path.expanduser("~/.var/app/io.github.ryubing.Ryujinx/config/Ryujinx/Config.json")

def get_ryubing_game_dirs() -> List[str]:
    """Get the Ryubing game directories from config."""
    config_path = get_ryubing_config_path()
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            game_dirs = config.get('game_dirs', [])
            if game_dirs:
                return game_dirs
        except (json.JSONDecodeError, KeyError):
            pass
    # Fallback to default
    return [os.path.expanduser("~/.var/app/io.github.ryubing.Ryujinx/data/Ryujinx/games")]

def list_ryubing_games() -> List[Tuple[str, str]]:
    """List all games in Ryubing."""
    games = []
    game_dirs = get_ryubing_game_dirs()
    
    # Supported file extensions for Switch games
    extensions = ['.nsp', '.xci', '.nca', '.nro']
    
    for games_dir in game_dirs:
        if not os.path.exists(games_dir):
            continue
        
        for root, dirs, files in os.walk(games_dir):
            for file in files:
                if any(file.lower().endswith(ext) for ext in extensions):
                    game_path = os.path.join(root, file)
                    # Use filename without extension as game name, removing bracketed parts
                    game_name = re.sub(r'\[.*?\]', '', os.path.splitext(file)[0]).strip()
                    games.append((game_path, game_name))
    
    return games