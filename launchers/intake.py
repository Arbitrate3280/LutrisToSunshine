"""Complete launcher intake seam: detect, collect, select, and build commands."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from config.constants import LAUNCHER_NAMES
from config.registry import LAUNCHER_REGISTRY
from config.types import GameSelection
from utils.utils import dedupe_selected_games_by_name, normalize_game_name_for_dedup


@dataclass(frozen=True)
class SelectionResult:
    games: List[GameSelection]
    skipped_duplicates: List[Tuple[GameSelection, GameSelection]]
    invalid_games: List[Tuple[GameSelection, str]]


def detect_launchers() -> Dict[str, bool]:
    return {name: LAUNCHER_REGISTRY[name]["detect"]() for name in LAUNCHER_NAMES}


def collect_games(detected_launchers: Dict[str, bool]) -> List[GameSelection]:
    with ThreadPoolExecutor() as executor:
        futures = {
            name: executor.submit(LAUNCHER_REGISTRY[name]["list"])
            for name in LAUNCHER_NAMES
            if detected_launchers.get(name)
        }
        games: List[GameSelection] = []
        for source_name, future in futures.items():
            games.extend(future.result())
    return games


def select_games(
    games: List[GameSelection],
    selected_indices: Iterable[int],
    existing_names: Iterable[str],
) -> SelectionResult:
    existing = {normalize_game_name_for_dedup(name) for name in existing_names}
    selected = [
        games[index]
        for index in selected_indices
        if 0 <= index < len(games)
        and normalize_game_name_for_dedup(games[index].game_name) not in existing
    ]
    deduped, skipped = dedupe_selected_games_by_name(selected)
    valid: List[GameSelection] = []
    invalid: List[Tuple[GameSelection, str]] = []
    for game in deduped:
        entry = LAUNCHER_REGISTRY.get(game.display_source)
        reason = entry["validate"](game) if entry else None
        if reason:
            invalid.append((game, reason))
            continue
        valid.append(game)
    return SelectionResult(valid, skipped, invalid)


def build_game_command(game: GameSelection) -> Optional[str]:
    entry = LAUNCHER_REGISTRY.get(game.display_source)
    if entry is None:
        return None
    return entry["command"](game)


def submit_games(
    games: List[GameSelection],
    *,
    download_images: bool,
    api_key: Optional[str],
    default_image: str,
    download_image_fn: Callable[[str, str], str],
    submit_game_fn: Callable[[str, str, str], None],
) -> bool:
    """Download covers and submit the selected games as one intake operation."""
    games_added = False
    with ThreadPoolExecutor() as executor:
        futures = {}
        for game in games:
            command = build_game_command(game)
            if not command:
                print(f"Warning: Unable to determine launch command for {game.game_name}. Skipping.")
                continue
            if download_images and api_key:
                future = executor.submit(download_image_fn, game.game_name, api_key)
                futures[future] = game
            else:
                submit_game_fn(game.game_name, command, default_image)
                games_added = True

        for future in as_completed(futures):
            game = futures[future]
            try:
                image_path = future.result()
            except Exception as error:
                print(f"Error downloading image for {game.game_name}: {error}")
                image_path = default_image
            command = build_game_command(game)
            if not command:
                print(f"Warning: Unable to determine launch command for {game.game_name}. Skipping.")
                continue
            submit_game_fn(game.game_name, command, image_path)
            games_added = True
    return games_added
