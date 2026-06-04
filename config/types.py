"""Domain types shared across the tool."""

from typing import Any, Dict, NamedTuple, Union


class GameSelection(NamedTuple):
    game_id: str
    game_name: str
    display_source: str
    source: Union[str, Dict[str, Any]]

