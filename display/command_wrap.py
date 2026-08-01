"""Launch-command wrapping helpers for Sunshine apps.

Pure base64/shlex string logic extracted from ``display/manager.py`` so
``sunshine/`` does not depend on the display lifecycle module.
"""

import base64
import json
import shlex
from pathlib import Path
from typing import Dict, List, Optional

CONFIG_ROOT = Path("~/.config/lutristosunshine").expanduser()
BIN_ROOT = CONFIG_ROOT / "bin"
DISPLAY_STATE_PATH = CONFIG_ROOT / "display" / "display.json"
LEGACY_STATE_PATH = CONFIG_ROOT / "virtualdisplay" / "virtualdisplay.json"
HEADLESS_PREP_PREFIX = "headless:"


def _display_enabled() -> bool:
    """Check display enabled flag without importing manager.

    Mirrors ``manager.load_state()``: DISPLAY_STATE_PATH wins when it
    exists; a missing or corrupt file yields disabled, never falling
    through to the legacy path.
    """
    state_path = DISPLAY_STATE_PATH if DISPLAY_STATE_PATH.exists() else LEGACY_STATE_PATH
    if not state_path.exists():
        return False
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    return bool(data.get("enabled"))


def get_launch_app_script() -> str:
    return str(BIN_ROOT / "lutristosunshine-launch-app.sh")


def get_headless_prep_script() -> str:
    return str(BIN_ROOT / "lutristosunshine-run-headless-prep.sh")


def get_app_prep_commands() -> List[Dict[str, str]]:
    if not _display_enabled():
        return []
    return [
        {
            "do": str(BIN_ROOT / "lutristosunshine-set-resolution.sh"),
            "undo": str(BIN_ROOT / "lutristosunshine-reset-resolution.sh"),
        }
    ]


def wrap_command(command: Optional[str], origin: str = "cmd", exit_timeout: int = 5) -> Optional[str]:
    if not command:
        return command
    if is_wrapped_command(command):
        return command
    encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
    timeout = max(0, int(exit_timeout))
    return f"{get_launch_app_script()} {shlex.quote(origin)} {shlex.quote(str(timeout))} {shlex.quote(encoded)}"


def _wrapped_command_parts(command: Optional[str]) -> Optional[List[str]]:
    if not command or not is_wrapped_command(command):
        return None
    try:
        return shlex.split(command or "")
    except ValueError:
        return None


def get_wrapped_command_exit_timeout(command: Optional[str], default: int = 5) -> int:
    parts = _wrapped_command_parts(command)
    if not parts:
        return default
    if len(parts) >= 4 and parts[2].isdigit():
        return int(parts[2])
    return default


def unwrap_command(command: Optional[str]) -> Optional[str]:
    if not command:
        return command
    if not is_wrapped_command(command):
        return command
    parts = _wrapped_command_parts(command)
    if not parts:
        return command
    if len(parts) < 2:
        return command
    encoded_part = parts[1]
    if len(parts) >= 4 and parts[2].isdigit():
        encoded_part = parts[3]
    elif len(parts) >= 3:
        encoded_part = parts[2]
    try:
        return base64.b64decode(encoded_part).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return command


def is_wrapped_command(command: Optional[str]) -> bool:
    if not command:
        return False
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if not parts:
        return False
    return parts[0] == get_launch_app_script()


def get_wrapped_command_origin(command: Optional[str]) -> Optional[str]:
    parts = _wrapped_command_parts(command)
    if not parts:
        return None
    if len(parts) >= 3:
        return parts[1]
    return "cmd"


def wrap_headless_prep_command(command: Optional[str]) -> Optional[str]:
    if not command:
        return command
    if is_headless_prep_wrapped(command):
        return command
    encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
    return f"{get_headless_prep_script()} {shlex.quote(encoded)}"


def _wrapped_headless_prep_parts(command: Optional[str]) -> Optional[List[str]]:
    if not command or not is_headless_prep_wrapped(command):
        return None
    try:
        return shlex.split(command or "")
    except ValueError:
        return None


def unwrap_headless_prep_command(command: Optional[str]) -> Optional[str]:
    if not command:
        return command
    if not is_headless_prep_wrapped(command):
        return command
    parts = _wrapped_headless_prep_parts(command)
    if not parts or len(parts) < 2:
        return command
    try:
        return base64.b64decode(parts[1]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return command


def is_headless_prep_wrapped(command: Optional[str]) -> bool:
    if not command:
        return False
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if not parts:
        return False
    return parts[0] == get_headless_prep_script()
