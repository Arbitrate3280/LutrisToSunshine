"""Single registry of launcher capabilities derived from LAUNCHER_NAMES.

Every supported launcher has exactly one entry here with detect/list/command/validate
functions.  This eliminates the N+1 copies of the launcher list that were
previously scattered across ``lutristosunshine.py``, ``config/constants.py``,
and ``utils/utils.py``.
"""

from typing import Callable, List, Optional, TypedDict

from config.constants import LAUNCHER_NAMES
from config.types import CommandBuilder, GameSelection

from launchers.steam import build_steam_command, detect_steam_installation, get_steam_command, list_steam_selections
from launchers.lutris import build_lutris_command, get_lutris_command, list_lutris_selections
from launchers.heroic import build_heroic_command, get_heroic_command, list_heroic_selections
from launchers.bottles import build_bottles_command, detect_bottles_installation, list_bottles_selections
from launchers.faugus import build_faugus_command, detect_faugus_installation, list_faugus_selections
from launchers.ryubing import build_ryubing_command, detect_ryubing_installation, list_ryubing_selections
from launchers.retroarch import build_retroarch_command, detect_retroarch_installation, list_retroarch_selections, validate_retroarch_selection
from launchers.eden import build_eden_command, detect_eden_installation, list_eden_selections


class LauncherEntry(TypedDict):
    detect: Callable[[], bool]
    list: Callable[[], List[GameSelection]]
    command: CommandBuilder
    validate: Callable[[GameSelection], Optional[str]]


def _valid_selection(_game: GameSelection) -> Optional[str]:
    return None


LAUNCHER_REGISTRY: dict[str, LauncherEntry] = {
    "Steam": {
        "detect": lambda: bool(get_steam_command() if detect_steam_installation()[0] else ""),
        "list": list_steam_selections,
        "command": build_steam_command,
        "validate": _valid_selection,
    },
    "Lutris": {
        "detect": lambda: get_lutris_command() is not None,
        "list": list_lutris_selections,
        "command": build_lutris_command,
        "validate": _valid_selection,
    },
    "Heroic": {
        "detect": lambda: bool(get_heroic_command()[0]),
        "list": list_heroic_selections,
        "command": build_heroic_command,
        "validate": _valid_selection,
    },
    "Bottles": {
        "detect": detect_bottles_installation,
        "list": list_bottles_selections,
        "command": build_bottles_command,
        "validate": _valid_selection,
    },
    "Faugus": {
        "detect": detect_faugus_installation,
        "list": list_faugus_selections,
        "command": build_faugus_command,
        "validate": _valid_selection,
    },
    "Ryubing": {
        "detect": detect_ryubing_installation,
        "list": list_ryubing_selections,
        "command": build_ryubing_command,
        "validate": _valid_selection,
    },
    "RetroArch": {
        "detect": detect_retroarch_installation,
        "list": list_retroarch_selections,
        "command": build_retroarch_command,
        "validate": validate_retroarch_selection,
    },
    "Eden": {
        "detect": detect_eden_installation,
        "list": list_eden_selections,
        "command": build_eden_command,
        "validate": _valid_selection,
    },
}

assert LAUNCHER_REGISTRY.keys() == {*LAUNCHER_NAMES}, (
    "LAUNCHER_REGISTRY must have an entry for every launcher in LAUNCHER_NAMES"
)
