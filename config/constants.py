import os

# Constants
SUNSHINE_APPS_JSON_PATH = os.path.expanduser("~/.config/sunshine/apps.json")
COVERS_PATH = os.path.expanduser("~/.config/sunshine/covers")
DEFAULT_IMAGE = "default.png"
API_KEY_PATH = os.path.expanduser("~/.config/sunshine/steamgriddb_api_key.txt")

SOURCE_COLORS = {
    "Heroic": "\033[38;5;39m",  # #3CA6F9
    "Lutris": "\033[38;5;214m",  # #FFAF00
    "Bottles": "\033[38;5;203m"  # #F3544B
}
RESET_COLOR = "\033[0m"
