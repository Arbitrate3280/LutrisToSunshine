import os
import json
import base64
import requests
import getpass
import urllib3
import subprocess
import glob
import shlex
from typing import Tuple, Optional, Dict, List
from requests.utils import dict_from_cookiejar, cookiejar_from_dict
from config.constants import (
    DEFAULT_IMAGE,
    DEFAULT_SUNSHINE_HOST,
    DEFAULT_SUNSHINE_PORT,
)
from utils.utils import run_command
from launchers.lutris import get_lutris_command
from launchers.heroic import get_heroic_command
from launchers.faugus import get_faugus_command
from launchers.steam import get_steam_command
from launchers.retroarch import get_retroarch_command
from launchers.eden import get_eden_command
from display.manager import (
    HEADLESS_PREP_PREFIX,
    get_app_prep_commands,
    is_headless_prep_wrapped,
    get_wrapped_command_origin,
    get_wrapped_command_exit_timeout,
    is_wrapped_command,
    is_enabled as display_enabled,
    unwrap_headless_prep_command,
    unwrap_command,
    wrap_headless_prep_command,
    wrap_command,
)

#Remove SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

INSTALLATION_TYPE = None
SERVER_NAME = "sunshine"
AUTH_SESSION: Optional[requests.Session] = None
AUTH_TOKEN: Optional[str] = None
API_HOST_OVERRIDE: Optional[str] = None
API_PORT_OVERRIDE: Optional[int] = None

def set_installation_type(type_: str):
    global INSTALLATION_TYPE
    INSTALLATION_TYPE = type_

def set_server_name(name: str):
    global SERVER_NAME
    SERVER_NAME = name


def _normalize_api_host(host: Optional[str]) -> str:
    normalized = (host or "").strip()
    return normalized or DEFAULT_SUNSHINE_HOST


def _normalize_api_port(port: Optional[object]) -> int:
    try:
        normalized = int(port)
    except (TypeError, ValueError):
        raise ValueError("Port must be an integer.")
    if not 1 <= normalized <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    return normalized


def _api_connection_file_path() -> str:
    return os.path.join(_get_config_root(), "server_connection.json")


def _load_api_connection_settings() -> Dict[str, Dict[str, object]]:
    path = _api_connection_file_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as file:
            payload = json.load(file)
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_api_connection_settings(settings: Dict[str, Dict[str, object]]) -> None:
    os.makedirs(_get_config_root(), exist_ok=True)
    with open(_api_connection_file_path(), "w") as file:
        json.dump(settings, file, indent=2)


def set_api_connection(host: Optional[str] = None, port: Optional[object] = None) -> None:
    global API_HOST_OVERRIDE, API_PORT_OVERRIDE, AUTH_SESSION, AUTH_TOKEN
    API_HOST_OVERRIDE = _normalize_api_host(host) if host is not None else None
    API_PORT_OVERRIDE = _normalize_api_port(port) if port is not None else None
    AUTH_SESSION = None
    AUTH_TOKEN = None


def save_api_connection(host: Optional[str], port: Optional[object], server_name: Optional[str] = None) -> None:
    target_server = (server_name or SERVER_NAME or "sunshine").strip().lower()
    current_host, current_port = get_api_connection(server_name=target_server)
    settings = _load_api_connection_settings()
    settings[target_server] = {
        "host": _normalize_api_host(host if host is not None else current_host),
        "port": _normalize_api_port(port if port is not None else current_port),
    }
    _save_api_connection_settings(settings)


def _get_environment_api_host(server_name: str) -> Optional[str]:
    keys = [
        f"LUTRISTOSUNSHINE_{server_name.upper()}_HOST",
        "LUTRISTOSUNSHINE_API_HOST",
    ]
    for key in keys:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return None


def _get_environment_api_port(server_name: str) -> Optional[int]:
    keys = [
        f"LUTRISTOSUNSHINE_{server_name.upper()}_PORT",
        "LUTRISTOSUNSHINE_API_PORT",
    ]
    for key in keys:
        value = os.environ.get(key, "").strip()
        if not value:
            continue
        try:
            return _normalize_api_port(value)
        except ValueError:
            continue
    return None


def get_api_connection(server_name: Optional[str] = None) -> Tuple[str, int]:
    target_server = (server_name or SERVER_NAME or "sunshine").strip().lower()
    settings = _load_api_connection_settings()
    saved = settings.get(target_server, {}) if isinstance(settings.get(target_server, {}), dict) else {}

    host = (
        API_HOST_OVERRIDE
        or _get_environment_api_host(target_server)
        or _normalize_api_host(saved.get("host"))
    )

    saved_port = saved.get("port")
    try:
        normalized_saved_port = _normalize_api_port(saved_port) if saved_port is not None else DEFAULT_SUNSHINE_PORT
    except ValueError:
        normalized_saved_port = DEFAULT_SUNSHINE_PORT

    port = (
        API_PORT_OVERRIDE
        or _get_environment_api_port(target_server)
        or normalized_saved_port
    )
    return host, port


def get_api_url(server_name: Optional[str] = None) -> str:
    host, port = get_api_connection(server_name=server_name)
    return f"https://{host}:{port}"


def _server_supports_token_auth() -> bool:
    return SERVER_NAME != "apollo"


def get_server_display_name() -> str:
    return "Apollo" if SERVER_NAME == "apollo" else "Sunshine"


def _get_apollo_process_config_root() -> Optional[str]:
    try:
        output = subprocess.check_output(["pgrep", "-x", "apollo-bin"], stderr=subprocess.STDOUT).decode()
    except subprocess.CalledProcessError:
        return None

    for pid in output.split():
        cmdline_path = f"/proc/{pid}/cmdline"
        try:
            with open(cmdline_path, "rb") as cmdline_file:
                args = [part.decode() for part in cmdline_file.read().split(b"\0") if part]
        except (OSError, UnicodeDecodeError):
            continue

        for arg in reversed(args[1:]):
            expanded = os.path.expanduser(arg)
            if expanded.endswith(".conf"):
                return os.path.dirname(os.path.realpath(expanded))

    return None


def _get_apollo_config_root() -> str:
    process_root = _get_apollo_process_config_root()
    if process_root:
        return process_root

    apollo_root = os.path.expanduser("~/.config/apollo")
    if os.path.isdir(apollo_root):
        return apollo_root

    sunshine_root = os.path.expanduser("~/.config/sunshine")
    if os.path.isdir(sunshine_root):
        return sunshine_root

    return apollo_root


def _get_config_root() -> str:
    if INSTALLATION_TYPE == "flatpak":
        return os.path.expanduser("~/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine")
    if SERVER_NAME == "apollo":
        return _get_apollo_config_root()
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

def add_game_to_sunshine_api(
    game_name: str,
    cmd: str,
    image_path: str,
    prep_cmd: Optional[List[Dict[str, str]]] = None,
    detached: Optional[List[str]] = None,
) -> None:
    """Add a game to the active server configuration using the API."""
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
        "prep-cmd": prep_cmd or [],
        "detached": detached or [],
        "image-path": image_path
    }

    _, error = sunshine_api_request("POST", "/api/apps", json=payload)
    if error:
        print(f"Error adding {game_name} to {get_server_display_name()} via API: {error}")
    else:
        print(f"Added {game_name} to {get_server_display_name()}.")


def _get_display_prep_scripts() -> set[str]:
    managed_scripts = set()
    for command in get_app_prep_commands():
        do_cmd = command.get("do", "")
        undo_cmd = command.get("undo", "")
        if do_cmd:
            managed_scripts.add(do_cmd)
        if undo_cmd:
            managed_scripts.add(undo_cmd)
    return managed_scripts


def _normalize_prep_cmd(app: Dict, enable_display: bool) -> List[Dict]:
    prep_cmd = app.get("prep-cmd") or []
    managed_scripts = _get_display_prep_scripts()
    filtered = []
    for command in prep_cmd:
        if not isinstance(command, dict):
            continue
        do_cmd = command.get("do", "")
        undo_cmd = command.get("undo", "")
        if do_cmd in managed_scripts or undo_cmd in managed_scripts:
            continue
        normalized = dict(command)
        normalized["do"] = _normalize_single_prep_command(do_cmd, enable_display)
        normalized["undo"] = _normalize_single_prep_command(undo_cmd, enable_display)
        filtered.append(normalized)

    if enable_display:
        return get_app_prep_commands() + filtered
    return filtered


def _normalize_single_prep_command(command: str, enable_display: bool) -> str:
    if not command:
        return ""

    if enable_display:
        if is_headless_prep_wrapped(command):
            return command
        if command.startswith(HEADLESS_PREP_PREFIX):
            raw_command = command[len(HEADLESS_PREP_PREFIX):].lstrip()
            if not raw_command:
                return ""
            return wrap_headless_prep_command(raw_command) or command
        return command

    if is_headless_prep_wrapped(command):
        raw_command = unwrap_headless_prep_command(command)
        if not raw_command:
            return ""
        return f"{HEADLESS_PREP_PREFIX}{raw_command}"

    return command


def _normalize_app_payload(app: Dict) -> Dict:
    payload = dict(app)
    payload["cmd"] = payload.get("cmd") or ""
    payload["output"] = payload.get("output") or ""
    payload["detached"] = payload.get("detached") or []
    payload["prep-cmd"] = payload.get("prep-cmd") or []
    payload["exclude-global-prep-cmd"] = payload.get("exclude-global-prep-cmd", False)
    payload["elevated"] = payload.get("elevated", False)
    payload["auto-detach"] = payload.get("auto-detach", True)
    payload["wait-all"] = payload.get("wait-all", True)
    payload["exit-timeout"] = payload.get("exit-timeout", 5)
    payload["image-path"] = payload.get("image-path") or ""
    return payload


def _dedupe_commands(commands: List[str]) -> List[str]:
    unique_commands = []
    seen = set()
    for command in commands:
        if not command or command in seen:
            continue
        unique_commands.append(command)
        seen.add(command)
    return unique_commands


def _unwrap_with_origin(command: str, field_origin: str) -> Tuple[str, str]:
    if not command:
        return "", field_origin
    if not is_wrapped_command(command):
        return command, field_origin

    origin = get_wrapped_command_origin(command) or field_origin
    # Legacy wrappers only encoded the command, so preserve the field placement.
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = []
    if len(parts) < 3:
        origin = field_origin

    unwrapped = unwrap_command(command) or ""
    return unwrapped, origin


def _select_display_primary_command(app: Dict) -> Tuple[str, str, List[str], int]:
    cmd, cmd_origin = _unwrap_with_origin(app.get("cmd") or "", "cmd")
    if cmd:
        timeout = get_wrapped_command_exit_timeout(app.get("cmd") or "", app.get("exit-timeout", 5))
        remaining = []
        for command in app.get("detached") or []:
            unwrapped, _ = _unwrap_with_origin(command, "detached")
            if unwrapped:
                remaining.append(unwrapped)
        return cmd, cmd_origin, remaining, timeout

    detached_commands: List[str] = []
    detached_timeout = app.get("exit-timeout", 5)
    for command in app.get("detached") or []:
        unwrapped, _ = _unwrap_with_origin(command, "detached")
        if not unwrapped:
            continue
        detached_commands.append(unwrapped)
        if len(detached_commands) == 1:
            detached_timeout = get_wrapped_command_exit_timeout(command, app.get("exit-timeout", 5))

    if not detached_commands:
        return "", "cmd", [], app.get("exit-timeout", 5)
    return detached_commands[0], "detached", detached_commands[1:], detached_timeout


def _enable_display_launch(app: Dict) -> Tuple[str, List[str]]:
    primary_command, origin, detached_commands, exit_timeout = _select_display_primary_command(app)
    wrapped_primary = wrap_command(primary_command, origin, exit_timeout) or ""
    return wrapped_primary, _dedupe_commands(detached_commands)


def _disable_display_launch(app: Dict) -> Tuple[str, List[str]]:
    restored_cmd = ""
    restored_detached: List[str] = []

    cmd, origin = _unwrap_with_origin(app.get("cmd") or "", "cmd")
    if cmd:
        if origin == "detached":
            restored_detached.append(cmd)
        else:
            restored_cmd = cmd

    for command in app.get("detached") or []:
        unwrapped, detached_origin = _unwrap_with_origin(command, "detached")
        if not unwrapped:
            continue
        if detached_origin == "cmd" and not restored_cmd:
            restored_cmd = unwrapped
        else:
            restored_detached.append(unwrapped)

    return restored_cmd, _dedupe_commands(restored_detached)


def _transform_app_for_display(app: Dict, enable_display: bool) -> Dict:
    updated = _normalize_app_payload(app)
    if enable_display:
        updated["cmd"], updated["detached"] = _enable_display_launch(updated)
    else:
        updated["cmd"], updated["detached"] = _disable_display_launch(updated)
    updated["prep-cmd"] = _normalize_prep_cmd(updated, enable_display)
    return updated


def _iter_unwrapped_app_commands(app: Dict) -> List[str]:
    commands: List[str] = []
    cmd, _ = _unwrap_with_origin(app.get("cmd") or "", "cmd")
    if cmd:
        commands.append(cmd)
    for command in app.get("detached") or []:
        unwrapped, _ = _unwrap_with_origin(command, "detached")
        if unwrapped:
            commands.append(unwrapped)
    return commands


def _load_cached_auth_token() -> Optional[str]:
    token_path = _token_file_path()
    if not os.path.exists(token_path):
        return None
    try:
        with open(token_path, "r") as file:
            token = file.read().strip()
    except OSError:
        return None
    return token or None


def _get_full_sunshine_apps(allow_prompt: bool = True) -> Tuple[List[Dict], Optional[str]]:
    request_kwargs = {}
    if not allow_prompt:
        session = get_auth_session(allow_prompt=False)
        token = AUTH_TOKEN or _load_cached_auth_token()
        if session is None and token is None:
            return [], "No cached Sunshine authentication is available."
        if session is not None:
            request_kwargs["session"] = session
        if token is not None:
            request_kwargs["token"] = token

    data, error = sunshine_api_request("GET", "/api/apps", **request_kwargs)
    if error:
        return [], error

    apps = []
    for index, app in enumerate(data.get("apps", [])):
        if isinstance(app, dict):
            payload = dict(app)
            payload["index"] = index
            apps.append(payload)
    return apps, None


def get_display_blocked_apps() -> Tuple[List[Tuple[str, str]], Optional[str]]:
    return [], None


def reconcile_display_apps(enable_display: bool) -> Tuple[int, Optional[str]]:
    apps, error = _get_full_sunshine_apps()
    if error:
        return 0, error

    updated_count = 0
    for app in apps:
        transformed = _transform_app_for_display(app, enable_display)
        if transformed == _normalize_app_payload(app):
            continue
        _, update_error = sunshine_api_request("POST", "/api/apps", json=transformed)
        if update_error:
            app_name = app.get("name", "Unknown App")
            return updated_count, f"Error updating {app_name}: {update_error}"
        updated_count += 1

    return updated_count, None

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

def _token_file_path() -> str:
    return os.path.join(get_credentials_path(), "auth_token.txt")

def _save_auth_token(token: str) -> None:
    os.makedirs(get_credentials_path(), exist_ok=True)
    with open(_token_file_path(), "w") as f:
        f.write(token)

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
        resp = session.get(f"{get_api_url()}/api/apps", verify=False, timeout=10)
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False

def _validate_token(token: str) -> bool:
    if not _server_supports_token_auth():
        return False
    try:
        resp = requests.get(
            f"{get_api_url()}/api/apps",
            headers={"Authorization": token},
            verify=False,
            timeout=10,
        )
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False

def get_auth_session(allow_prompt: bool = True) -> Optional[requests.Session]:
    """Retrieves or creates an authenticated session using cookies or login endpoints."""
    global AUTH_SESSION, AUTH_TOKEN
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
        url = f"{get_api_url()}{endpoint}"
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

    if not _server_supports_token_auth():
        print("Error: Authentication failed. Could not obtain a valid session.")
        return None

    # Fallback: use basic auth on the session for Sunshine.
    session = requests.Session()
    session.auth = (username, password)
    if _validate_session(session):
        auth_header = f"{username}:{password}"
        encoded_auth = base64.b64encode(auth_header.encode()).decode()
        token = f"Basic {encoded_auth}"
        _save_auth_token(token)
        AUTH_TOKEN = token
        AUTH_SESSION = session
        return session

    print("Error: Authentication failed. Could not obtain a valid session.")
    return None

def ensure_authenticated(allow_prompt: bool = True) -> bool:
    """Ensures the active server has either a valid session or token available."""
    global AUTH_TOKEN

    session = get_auth_session(allow_prompt=False)
    if session is not None:
        return True

    if _server_supports_token_auth():
        token = AUTH_TOKEN or _load_cached_auth_token()
        if token and _validate_token(token):
            AUTH_TOKEN = token
            return True

    if not allow_prompt:
        return False

    session = get_auth_session(allow_prompt=True)
    if session is not None:
        return True

    if _server_supports_token_auth():
        return get_auth_token() is not None

    return False

def get_auth_token() -> Optional[str]:
    """Retrieves or generates an authentication token."""
    global AUTH_TOKEN
    token_path = _token_file_path()

    if not _server_supports_token_auth():
        return None

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
    _save_auth_token(token)

    AUTH_TOKEN = token
    return token


def build_game_command(game_id: str, runner) -> Optional[str]:
    if runner == "Lutris":
        lutris_cmd = get_lutris_command()
        return f"{lutris_cmd} lutris:rungameid/{game_id}"
    if runner in ["legendary", "gog", "nile", "sideload"]:
        heroic_cmd, _ = get_heroic_command()
        return f"{heroic_cmd} heroic://launch/{runner}/{game_id} --no-gui --no-sandbox"
    if runner == "Steam":
        steam_cmd = get_steam_command()
        return f"{steam_cmd} steam://run/{game_id}"
    if runner == "Ryubing":
        return f"flatpak run io.github.ryubing.Ryujinx \"{game_id}\""
    if runner == "Eden":
        eden_cmd = get_eden_command()
        if not eden_cmd:
            return None
        return f'{eden_cmd} -f -g "{game_id}"'
    if isinstance(runner, dict) and runner.get("type") == "Faugus":
        faugus_cmd = get_faugus_command()
        return f"{faugus_cmd} --game {shlex.quote(game_id)}"
    if isinstance(runner, dict) and runner.get("type") == "RetroArch":
        core_path = runner.get("core_path", "")
        core_path = os.path.expanduser(core_path) if core_path else core_path
        retroarch_cmd = get_retroarch_command()
        if not retroarch_cmd or not core_path:
            return None
        return f'{retroarch_cmd} -L "{core_path}" "{game_id}"'
    return f'flatpak run --command=bottles-cli com.usebottles.bottles run -b "{runner}" -p "{game_id}"'

def add_game_to_sunshine(game_id: str, game_name: str, image_path: str, runner) -> None:
    """Add a game to the Sunshine configuration."""
    cmd = build_game_command(game_id, runner)
    if not cmd:
        print(f"Warning: Unable to determine launch command for {game_name}. Skipping.")
        return

    # Prefix commands with flatpak-spawn --host if Sunshine is installed as Flatpak
    if INSTALLATION_TYPE == "flatpak":
        cmd = f"flatpak-spawn --host {cmd}"

    enable_display = SERVER_NAME == "sunshine" and display_enabled()

    if enable_display:
        cmd = wrap_command(cmd, "cmd") or cmd

    prep_cmd = get_app_prep_commands() if enable_display else []

    # Use the API instead of directly modifying apps.json
    add_game_to_sunshine_api(game_name, cmd, image_path, prep_cmd=prep_cmd, detached=[])

def get_existing_apps() -> List[Dict]:
    """Retrieves the list of existing apps from the active server API."""
    data, error = sunshine_api_request("GET", "/api/apps")
    if error:
        print(f"Error retrieving existing apps from {get_server_display_name()} API: {error}")
        return []

    existing_apps = []
    apps_list = []
    if data is not None:
        apps_list = data.get("apps", [])
    else:
        print(f"Warning: No data received from {get_server_display_name()} API.")

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
    url = f"{get_api_url()}{endpoint}"
    session = kwargs.pop("session", None) or AUTH_SESSION
    token = kwargs.pop("token", None) or AUTH_TOKEN
    headers = kwargs.pop("headers", {})

    if session is None:
        session = get_auth_session(allow_prompt=False)

    if session is None and token is None:
        if not ensure_authenticated(allow_prompt=True):
            return None, "Error: Could not obtain authentication token or session."
        session = AUTH_SESSION
        token = AUTH_TOKEN

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
