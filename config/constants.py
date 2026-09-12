# Single canonical ordered list of all supported launchers (highest priority first).
LAUNCHER_NAMES = [
    "Steam",
    "Lutris",
    "Heroic",
    "Bottles",
    "Faugus",
    "Ryubing",
    "RetroArch",
    "Eden",
    "Cemu",
]

SOURCE_PRIORITY = {name: idx for idx, name in enumerate(LAUNCHER_NAMES)}

# Constants
DEFAULT_IMAGE = "default.png"
DEFAULT_SUNSHINE_HOST = "localhost"
DEFAULT_SUNSHINE_PORT = 47990

SOURCE_COLORS = {
    "Heroic": "\033[38;5;39m",  # Blue 
    "Lutris": "\033[38;5;214m",  # Orange 
    "Bottles": "\033[38;5;203m",  # Red - wine/bottles theme
    "Steam": "\033[38;5;26m",  # Dark blue - Steam branding
    "Faugus": "\033[38;5;81m",  # Cyan - distinct Flatpak launcher highlight
    "Ryubing": "\033[38;5;196m",  # Bright red - Nintendo Switch theme
    "RetroArch": "\033[38;5;46m",  # Green - retro gaming theme
    "Eden": "\033[38;5;201m",  # Pink - distinct highlight for Eden
    "Cemu": "\033[38;5;226m",  # Yellow - Wii U GamePad highlight
}
RESET_COLOR = "\033[0m"

assert SOURCE_COLORS.keys() == {*LAUNCHER_NAMES}, (
    "SOURCE_COLORS must define a color for every launcher in LAUNCHER_NAMES"
)
