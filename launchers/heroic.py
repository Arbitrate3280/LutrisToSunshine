import os
import json
import shutil
import shlex
from typing import Dict, Iterator, Optional, List, Tuple
from config.types import DetectHints, GameSelection, LauncherSource
from utils.utils import normalize_detect_hints, path_basename_for_hint, run_command


def build_heroic_command(game: GameSelection) -> Optional[str]:
    command, _ = get_heroic_command()
    if not command:
        return None
    runner = str(game.source)
    return shlex.join(
        [*shlex.split(command), f"heroic://launch/{runner}/{game.game_id}", "--no-gui", "--no-sandbox"]
    )

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
    if run_command(["flatpak", "info", "com.heroicgameslauncher.hgl"]).returncode == 0:
        return "flatpak run com.heroicgameslauncher.hgl", "flatpak"
    elif shutil.which("heroic"):
        return "heroic", "native"
    else:
        return None, None


def _heroic_detect_hints(game: Dict[str, object]) -> Optional[DetectHints]:
    hints: DetectHints = {"env_marker": {"key": "HEROIC_APP_NAME"}}

    install = game.get("install")
    install_dir = str(game.get("install_path") or "").strip()
    exe = str(game.get("executable") or game.get("exe") or "").strip()
    if isinstance(install, dict):
        if not exe:
            exe = str(install.get("executable") or "").strip()
        if not install_dir:
            install_dir = str(install.get("path") or install.get("folder") or "").strip()

    if exe:
        is_windows_path = exe[1:2] == ":"
        if not os.path.isabs(exe) and not is_windows_path and install_dir:
            exe = os.path.join(install_dir, exe.lstrip("/\\"))
        if os.path.isabs(exe):
            exe = os.path.expanduser(exe)
        hints["exe"] = exe
        hints["process_name"] = path_basename_for_hint(exe)
    if install_dir and os.path.isabs(install_dir):
        hints["install_dir"] = os.path.expanduser(install_dir)
    return normalize_detect_hints(hints)


def _iter_heroic_apps() -> Iterator[Tuple[str, str, str, Optional[DetectHints]]]:
    """Yield (app_id, title, runner, hints) for every installed Heroic game."""
    heroic_cmd, installation_type = get_heroic_command()
    if not heroic_cmd or not installation_type:
        return

    for runner, path in HEROIC_PATHS[installation_type].items():
        if not os.path.exists(path):
            continue
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
                                    yield app_id, title, runner, _heroic_detect_hints(game)
                    elif "games" in data:
                        # Handling sideloaded games
                        for game in data["games"]:
                            if isinstance(game, dict):
                                app_id = game.get("app_name")
                                title = game.get("title")
                                if app_id and title:
                                    yield app_id, title, "sideload", _heroic_detect_hints(game)
                    else:
                        # Handling Legendary games
                        for app_id, game in data.items():
                            if isinstance(game, dict):
                                title = game.get("title") or game.get("app_name")
                                if app_id and title:
                                    yield app_id, title, runner, _heroic_detect_hints(game)
                elif isinstance(data, list):
                    # In case there are other list-based structures in future
                    for game in data:
                        if isinstance(game, dict):
                            app_id = game.get("appName") or game.get("app_name") or game.get("id")
                            install_path = game.get("install_path") or game.get("path")
                            if install_path:
                                title = install_path.split('/')[-1]
                            else:
                                title = app_id
                            if app_id and title:
                                yield app_id, title, runner, _heroic_detect_hints(game)
        except json.JSONDecodeError:
            print(f"Error parsing JSON file at {path}")


def list_heroic_games() -> List[Tuple[str, str, str, str]]:
    """List all games in Heroic."""
    return [(app_id, title, "Heroic", runner) for app_id, title, runner, _ in _iter_heroic_apps()]


def list_heroic_selections() -> List[GameSelection]:
    return [
        GameSelection(app_id, title, "Heroic", LauncherSource(runner), hints)
        for app_id, title, runner, hints in _iter_heroic_apps()
    ]
