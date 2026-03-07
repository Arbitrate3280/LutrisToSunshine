import os
import json
import base64
import requests
import getpass
import urllib3
import subprocess
import glob
from typing import Tuple, Optional, Dict, List
from requests.utils import dict_from_cookiejar, cookiejar_from_dict
from config.constants import DEFAULT_IMAGE, SUNSHINE_API_URL
from utils.utils import run_command
from launchers.lutris import get_lutris_command
from launchers.heroic import get_heroic_command
from launchers.steam import get_steam_command
from launchers.retroarch import get_retroarch_command
from launchers.eden import get_eden_command

#Remove SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

INSTALLATION_TYPE = None
SERVER_NAME = "sunshine"
AUTH_SESSION: Optional[requests.Session] = None
AUTH_TOKEN: Optional[str] = None

def set_installation_type(type_: str):
    global INSTALLATION_TYPE
    INSTALLATION_TYPE = type_

def set_server_name(name: str):
    global SERVER_NAME
    SERVER_NAME = name

def _get_config_root() -> str:
    if INSTALLATION_TYPE == "flatpak":
        return os.path.expanduser("~/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine")
    if SERVER_NAME == "apollo":
        apollo_root = os.path.expanduser("~/.config/apollo")
        if os.path.isdir(apollo_root):
            return apollo_root
    return os.path.expanduser("~/.config/sunshine")

def get_covers_path():
    return os.path.join(_get_config_root(), "covers")

def get_api_key_path():
    return os.path.join(_get_config_root(), "steamgriddb_api_key.txt")

def get_credentials_path():
    return os.path.join(_get_config_root(), "credentials")

def detect_sunshine_installation() -> Tuple[bool, str]:
    """Detect if Sunshine is installed and how."""
    # Check for Flatpak installation
    if run_command("flatpak list | grep dev.lizardbyte.app.Sunshine").returncode == 0:
        return True, "flatpak"
    # Check for native installation
    elif run_command("which sunshine").returncode == 0:
        return True, "native"
    # Check for AppImage installation
    else:
        appimage_paths = (
            glob.glob(os.path.expanduser("~/sunshine.AppImage")) +
            glob.glob(os.path.expanduser("~/.local/share/applications/sunshine.AppImage")) +
            glob.glob(os.path.expanduser("~/AppImages/sunshine.AppImage")) +
            glob.glob(os.path.expanduser("~/bin/sunshine.AppImage")) +
            glob.glob(os.path.expanduser("~/Downloads/sunshine.AppImage"))
        )
        if appimage_paths:
            return True, "appimage"
        return False, ""

def detect_apollo_installation() -> bool:
    """Detect if Apollo is installed (native only)."""
    return run_command("which apollo").returncode == 0

def get_running_servers() -> List[str]:
    """Return a list of running servers detected in process list."""
    try:
        output = subprocess.check_output(["ps", "-A"], stderr=subprocess.STDOUT).decode().lower()
    except subprocess.CalledProcessError:
        return []
    running = []
    if "sunshine" in output:
        running.append("sunshine")
    if "apollo" in output:
        running.append("apollo")
    return running

def is_server_running(name: Optional[str] = None) -> bool:
    running = get_running_servers()
    if name is None:
        return bool(running)
    return name in running

def add_game_to_sunshine_api(game_name: str, cmd: str, image_path: str) -> None:
    """Add a game to the Sunshine configuration using the API."""
    payload = {
        "name": game_name,
        "output": "",
        "cmd": cmd,
        "index": -1,
        "exclude-global-prep-cmd": False,
        "elevated": False,
        "auto-detach": True,
        "wait-all": True,
        "exit-timeout": 5,
        "prep-cmd": [],
        "detached": [],
        "image-path": image_path
    }

    _, error = sunshine_api_request("POST", "/api/apps", json=payload)
    if error:
        print(f"Error adding {game_name} to Sunshine via API: {error}")
    else:
        print(f"Added {game_name} to Sunshine.")

def get_sunshine_credentials() -> Tuple[str, str]:
    """Prompts the user for their Sunshine username and password."""
    username = input("Enter your Sunshine/Apollo username: ")
    password = getpass.getpass("Enter your Sunshine/Apollo password: ")
    return username, password

def is_sunshine_running() -> bool:
    """Checks if Sunshine or Apollo is currently running."""
    return is_server_running()

def _cookies_file_path():
    return os.path.join(get_credentials_path(), "cookies.json")

def _save_session_cookies(session: requests.Session):
    os.makedirs(get_credentials_path(), exist_ok=True)
    cookies_dict = dict_from_cookiejar(session.cookies)
    try:
        with open(_cookies_file_path(), "w") as f:
            json.dump(cookies_dict, f)
    except Exception as e:
        print(f"Warning: Failed to save cookies: {e}")

def _load_session_from_cookies() -> requests.Session:
    session = requests.Session()
    cookie_file = _cookies_file_path()
    if os.path.exists(cookie_file):
        try:
            with open(cookie_file, "r") as f:
                cookies_dict = json.load(f)
            session.cookies = cookiejar_from_dict(cookies_dict)
        except Exception:
            try:
                os.remove(cookie_file)
            except Exception:
                pass
    return session

def _validate_session(session: requests.Session) -> bool:
    try:
        resp = session.get(f"{SUNSHINE_API_URL}/api/apps", verify=False, timeout=10)
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False

def _validate_token(token: str) -> bool:
    try:
        resp = requests.get(
            f"{SUNSHINE_API_URL}/api/apps",
            headers={"Authorization": token},
            verify=False,
            timeout=10,
        )
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False

def get_auth_session(allow_prompt: bool = True) -> Optional[requests.Session]:
    """Retrieves or creates an authenticated session using cookies or basic auth."""
    global AUTH_SESSION
    if AUTH_SESSION and _validate_session(AUTH_SESSION):
        return AUTH_SESSION

    session = _load_session_from_cookies()
    if _validate_session(session):
        AUTH_SESSION = session
        return session

    if not allow_prompt:
        return None

    if not is_sunshine_running():
        print("Error: Sunshine or Apollo is not running. Please start it and try again.")
        return None

    username, password = get_sunshine_credentials()
    if not username or not password:
        return None

    session = requests.Session()
    login_endpoints = [
        "/api/login",
        "/login",
        "/auth/login",
        "/api/auth/login",
    ]
    login_payloads = [
        ("json", {"username": username, "password": password}),
        ("form", {"username": username, "password": password}),
    ]
    for endpoint in login_endpoints:
        url = f"{SUNSHINE_API_URL}{endpoint}"
        for mode, payload in login_payloads:
            try:
                if mode == "json":
                    session.post(url, json=payload, verify=False, timeout=10)
                else:
                    session.post(url, data=payload, verify=False, timeout=10)
            except requests.exceptions.RequestException:
                continue
            if _validate_session(session):
                _save_session_cookies(session)
                AUTH_SESSION = session
                return session

    # Fallback: use basic auth on the session.
    session = requests.Session()
    session.auth = (username, password)
    if _validate_session(session):
        AUTH_SESSION = session
        return session

    print("Error: Authentication failed. Could not obtain a valid session.")
    return None

def get_auth_token() -> Optional[str]:
    """Retrieves or generates an authentication token."""
    global AUTH_TOKEN
    token_path = os.path.join(get_credentials_path(), "auth_token.txt")

    # Check if Sunshine is running BEFORE attempting any authentication
    if not is_sunshine_running():
        print("Error: Sunshine or Apollo is not running. Please start it and try again.")
        return None

    # Check for an existing token (only if Sunshine is running)
    if os.path.exists(token_path):
        with open(token_path, 'r') as f:
            token = f.read().strip()

        # Validate the existing token
        if not _validate_token(token):
            print(f"Error: Existing token is invalid. Please re-enter your credentials.")
            os.remove(token_path)  # Remove the invalid token file
        else:
            AUTH_TOKEN = token
            return token

    # If no valid token exists, prompt for credentials (only if Sunshine is running)
    username, password = get_sunshine_credentials()
    if not username or not password:
        return None

    auth_header = f"{username}:{password}"
    encoded_auth = base64.b64encode(auth_header.encode()).decode()
    token = f"Basic {encoded_auth}"

    # Validate the new token
    if not _validate_token(token):
        print(f"Error: Authentication failed. Please check your credentials.")
        return None

    # Save the new token if it's valid
    os.makedirs(get_credentials_path(), exist_ok=True)
    with open(token_path, 'w') as f:
        f.write(token)

    AUTH_TOKEN = token
    return token

def add_game_to_sunshine(game_id: str, game_name: str, image_path: str, runner) -> None:
    """Add a game to the Sunshine configuration."""
    if runner == "Lutris":
        lutris_cmd = get_lutris_command()
        cmd = f"{lutris_cmd} lutris:rungameid/{game_id}"
    elif runner in ["legendary", "gog", "nile", "sideload"]:
        heroic_cmd, _ = get_heroic_command()
        cmd = f"{heroic_cmd} heroic://launch/{runner}/{game_id} --no-gui --no-sandbox"
    elif runner == "Steam":
        steam_cmd = get_steam_command()
        cmd = f"{steam_cmd} steam://run/{game_id}"
    elif runner == "Ryubing":
        cmd = f"flatpak run io.github.ryubing.Ryujinx \"{game_id}\""
    elif runner == "Eden":
        eden_cmd = get_eden_command()
        if not eden_cmd:
            print(f"Warning: Unable to determine Eden launch command for {game_name}. Skipping.")
            return
        cmd = f'{eden_cmd} -f -g "{game_id}"'
    elif isinstance(runner, dict) and runner.get("type") == "RetroArch":
        core_path = runner.get("core_path", "")
        core_path = os.path.expanduser(core_path) if core_path else core_path
        retroarch_cmd = get_retroarch_command()
        if not retroarch_cmd or not core_path:
            print(f"Warning: Unable to determine RetroArch launch command for {game_name}. Skipping.")
            return
        cmd = f'{retroarch_cmd} -L "{core_path}" "{game_id}"'
    else:  # Bottles
        cmd = f'flatpak run --command=bottles-cli com.usebottles.bottles run -b "{runner}" -p "{game_id}"'

    # Prefix commands with flatpak-spawn --host if Sunshine is installed as Flatpak
    if INSTALLATION_TYPE == "flatpak":
        cmd = f"flatpak-spawn --host {cmd}"

    # Use the API instead of directly modifying apps.json
    add_game_to_sunshine_api(game_name, cmd, image_path)

def get_existing_apps() -> List[Dict]:
    """Retrieves the list of existing apps from the Sunshine API."""
    data, error = sunshine_api_request("GET", "/api/apps")
    if error:
        print(f"Error retrieving existing apps from Sunshine API: {error}")
        return []

    existing_apps = []
    apps_list = []
    if data is not None:
        apps_list = data.get("apps", [])
    else:
        print("Warning: No data received from Sunshine API.")

    if isinstance(apps_list, list):
        for app_data in apps_list:
            if isinstance(app_data, dict) and "name" in app_data:
                existing_apps.append({"name": app_data["name"]})
    else:
        print("Warning: Unexpected data structure in API response.")

    return existing_apps

def sunshine_api_request(method, endpoint, **kwargs):
    """Makes an API request to Sunshine.

    Args:
        method (str): The HTTP method (GET, POST, etc.)
        endpoint (str): The API endpoint.
        **kwargs: Additional keyword arguments for the requests.request() function.

    Returns:
        Tuple[Optional[Dict], Optional[str]]: A tuple containing the JSON response data 
                                              (if successful) and an error message (if any).
    """
    url = f"{SUNSHINE_API_URL}{endpoint}"
    session = kwargs.pop("session", None) or AUTH_SESSION
    token = kwargs.pop("token", None) or AUTH_TOKEN
    headers = kwargs.pop("headers", {})

    if session is None:
        session = get_auth_session(allow_prompt=False)

    if session:
        try:
            response = session.request(method, url, headers=headers, verify=False, **kwargs)
            response.raise_for_status()
            try:
                return response.json(), None
            except ValueError:
                return {"text": response.text}, None
        except requests.exceptions.RequestException as e:
            return None, str(e)

    if token is None:
        token = get_auth_token()

    if not token:
        return None, "Error: Could not obtain authentication token or session."

    headers = {**headers, "Authorization": token}
    try:
        response = requests.request(method, url, headers=headers, verify=False, **kwargs)
        response.raise_for_status()
        try:
            return response.json(), None
        except ValueError:
            return {"text": response.text}, None
    except requests.exceptions.RequestException as e:
        return None, str(e)
