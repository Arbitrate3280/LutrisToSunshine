"""Launch-command wrapping helpers for Sunshine apps.

Pure base64/shlex string logic extracted from ``display/manager.py`` so
``sunshine/`` does not depend on the display lifecycle module.
"""

import base64
import shlex
from pathlib import Path
from typing import Dict, List, Optional

CONFIG_ROOT = Path("~/.config/lutristosunshine").expanduser()
BIN_ROOT = CONFIG_ROOT / "bin"
HEADLESS_PREP_PREFIX = "headless:"
_MANAGED_BIN_ROOT = str(BIN_ROOT)


def routes_through_managed_scripts(value: str) -> bool:
    """Check whether a command invokes a script under :data:`BIN_ROOT`.

    Matches any invocation form (bare path, ``flatpak-spawn --host`` escape,
    ``env`` prefix), so callers can recognise tool-owned commands in payloads
    written by older releases instead of relying on exact string equality.
    """
    if not value:
        return False
    try:
        tokens = shlex.split(value)
    except ValueError:
        return "lutristosunshine-" in value
    return any(
        token == _MANAGED_BIN_ROOT or token.startswith(_MANAGED_BIN_ROOT + "/")
        for token in tokens
    )


def get_launch_app_script() -> str:
    return str(BIN_ROOT / "lutristosunshine-launch-app.sh")


def get_headless_prep_script() -> str:
    return str(BIN_ROOT / "lutristosunshine-run-headless-prep.sh")


def get_app_prep_commands(display_enabled: bool) -> List[Dict[str, str]]:
    if not display_enabled:
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
