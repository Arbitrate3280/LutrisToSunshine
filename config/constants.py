import os

# Constants
DEFAULT_IMAGE = "default.png"
DEFAULT_SUNSHINE_HOST = "localhost"
DEFAULT_SUNSHINE_PORT = 47990
SUNSHINE_API_URL = f"https://{DEFAULT_SUNSHINE_HOST}:{DEFAULT_SUNSHINE_PORT}"

SOURCE_COLORS = {
    "Heroic": "\033[38;5;39m",  # Blue 
    "Lutris": "\033[38;5;214m",  # Orange 
    "Bottles": "\033[38;5;203m",  # Red - wine/bottles theme
    "Steam": "\033[38;5;26m",  # Dark blue - Steam branding
    "Faugus": "\033[38;5;81m",  # Cyan - distinct Flatpak launcher highlight
    "Ryubing": "\033[38;5;196m",  # Bright red - Nintendo Switch theme
    "RetroArch": "\033[38;5;46m",  # Green - retro gaming theme
    "Eden": "\033[38;5;201m",  # Pink - distinct highlight for Eden
}
RESET_COLOR = "\033[0m"
