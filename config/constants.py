import os

# Constants
COVERS_PATH = os.path.expanduser("~/.config/sunshine/covers")
DEFAULT_IMAGE = "default.png"
API_KEY_PATH = os.path.expanduser("~/.config/sunshine/steamgriddb_api_key.txt")
SUNSHINE_STATE_JSON_PATH = os.path.expanduser("~/.config/sunshine/sunshine_state.json")
CREDENTIALS_PATH = os.path.expanduser("~/.config/sunshine/credentials")

SOURCE_COLORS = {
    "Heroic": "\033[38;5;39m",  # #3CA6F9
    "Lutris": "\033[38;5;214m",  # #FFAF00
    "Bottles": "\033[38;5;203m"  # #F3544B
}
RESET_COLOR = "\033[0m"

