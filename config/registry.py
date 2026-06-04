"""Single registry of launcher capabilities derived from LAUNCHER_NAMES.

Every supported launcher has exactly one entry here with detect/list/normalize
functions.  This eliminates the N+1 copies of the launcher list that were
previously scattered across ``lutristosunshine.py``, ``config/constants.py``,
and ``utils/utils.py``.
"""

from typing import Any, Callable, List, TypedDict

from config.constants import LAUNCHER_NAMES
from config.types import GameSelection

from launchers.steam import detect_steam_installation, list_steam_games, get_steam_command
from launchers.lutris import get_lutris_command, list_lutris_games
from launchers.heroic import list_heroic_games, get_heroic_command
from launchers.bottles import detect_bottles_installation, list_bottles_games
from launchers.faugus import detect_faugus_installation, list_faugus_games
from launchers.ryubing import detect_ryubing_installation, list_ryubing_games
from launchers.retroarch import detect_retroarch_installation, list_retroarch_games
from launchers.eden import detect_eden_installation, list_eden_games


class LauncherEntry(TypedDict):
    detect: Callable[[], bool]
    list: Callable[[], Any]
    normalize: Callable[[Any], List[GameSelection]]


def _normalize_pair(source: str, games) -> List[GameSelection]:
    return [GameSelection(gid, name, source, source) for gid, name in games]


def _normalize_retroarch(games) -> List[GameSelection]:
    return [GameSelection(path, name, "RetroArch", core) for path, name, core in games]


LAUNCHER_REGISTRY: dict[str, LauncherEntry] = {
    "Steam": {
        "detect": lambda: bool(get_steam_command() if detect_steam_installation()[0] else ""),
        "list": list_steam_games,
        "normalize": lambda g: _normalize_pair("Steam", g),
    },
    "Lutris": {
        "detect": lambda: get_lutris_command() is not None,
        "list": list_lutris_games,
        "normalize": lambda g: _normalize_pair("Lutris", g),
    },
    "Heroic": {
        "detect": lambda: bool(get_heroic_command()[0]),
        "list": list_heroic_games,
        "normalize": lambda g: [GameSelection(gid, name, "Heroic", runner) for gid, name, _, runner in g],
    },
    "Bottles": {
        "detect": detect_bottles_installation,
        "list": list_bottles_games,
        "normalize": lambda g: [GameSelection(gid, name, "Bottles", bottle) for gid, name, _, bottle in g],
    },
    "Faugus": {
        "detect": detect_faugus_installation,
        "list": list_faugus_games,
        "normalize": lambda g: [GameSelection(gid, name, "Faugus", runner) for gid, name, _, runner in g],
    },
    "Ryubing": {
        "detect": detect_ryubing_installation,
        "list": list_ryubing_games,
        "normalize": lambda g: _normalize_pair("Ryubing", g),
    },
    "RetroArch": {
        "detect": detect_retroarch_installation,
        "list": list_retroarch_games,
        "normalize": _normalize_retroarch,
    },
    "Eden": {
        "detect": detect_eden_installation,
        "list": list_eden_games,
        "normalize": lambda g: _normalize_pair("Eden", g),
    },
}

assert LAUNCHER_REGISTRY.keys() == {*LAUNCHER_NAMES}, (
    "LAUNCHER_REGISTRY must have an entry for every launcher in LAUNCHER_NAMES"
)
