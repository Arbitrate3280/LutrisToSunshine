"""Domain types shared across the tool."""

from dataclasses import dataclass
from typing import Callable, List, NamedTuple, Optional, TypedDict, Union


@dataclass(frozen=True)
class LauncherSource:
    name: str

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class FaugusSource:
    game_path: str
    prefix: str
    mangohud: bool
    gamemode: bool
    no_sleep: bool
    sdl_enabled: bool


@dataclass(frozen=True)
class RetroArchSource:
    core_path: str
    core_name: str


class SunshinePrepCommand(TypedDict, total=False):
    do: str
    undo: str
    elevated: bool


SunshineApp = TypedDict(
    "SunshineApp",
    {
        "name": str,
        "cmd": str,
        "output": str,
        "index": int,
        "exclude-global-prep-cmd": bool,
        "elevated": bool,
        "auto-detach": bool,
        "wait-all": bool,
        "exit-timeout": int,
        "prep-cmd": List[SunshinePrepCommand],
        "detached": List[str],
        "image-path": str,
        "working-dir": str,
    },
    total=False,
)


class SunshineAppReference(TypedDict):
    name: str


LauncherRunner = Union[LauncherSource, FaugusSource, RetroArchSource]


class GameSelection(NamedTuple):
    game_id: str
    game_name: str
    display_source: str
    source: LauncherRunner


CommandBuilder = Callable[[GameSelection], Optional[str]]
