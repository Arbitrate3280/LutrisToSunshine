"""Launch-command wrapping helpers for Sunshine apps.

Pure base64/shlex string logic extracted from ``display/manager.py`` so
``sunshine/`` does not depend on the display lifecycle module.
"""

import base64
import shlex
from pathlib import Path
from typing import Dict, List, Optional

from display.constants import FLATPAK_SPAWN_HOST_PREFIX

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


def _without_host_escape(parts: List[str]) -> List[str]:
    """Drop a leading ``flatpak-spawn --host`` escape from parsed tokens."""
    prefix = list(FLATPAK_SPAWN_HOST_PREFIX)
    if parts[: len(prefix)] == prefix:
        return parts[len(prefix):]
    return parts


def managed_wrapper_parts(command: Optional[str], script: Optional[str] = None) -> Optional[List[str]]:
    """Split a managed wrapper call into ``[script, origin?, timeout?, payload]``.

    Tolerates the ``flatpak-spawn --host`` escape older releases put in front
    of managed wrappers: without that, the escaped form looks like an ordinary
    game command and gets wrapped a second time.
    """
    if not command:
        return None
    try:
        parts = _without_host_escape(shlex.split(command))
    except ValueError:
        return None
    if not parts or parts[0] != (script or get_launch_app_script()):
        return None
    return parts


def _wrapper_payload(parts: List[str]) -> Optional[str]:
    """Decode the base64 payload of wrapper ``parts`` (legacy layouts included)."""
    if len(parts) < 2:
        return None
    encoded_part = parts[1]
    if len(parts) >= 4 and parts[2].isdigit():
        encoded_part = parts[3]
    elif len(parts) >= 3:
        encoded_part = parts[2]
    try:
        return base64.b64decode(encoded_part).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def get_wrapped_command_exit_timeout(command: Optional[str], default: int = 5) -> int:
    parts = managed_wrapper_parts(command)
    if not parts:
        return default
    if len(parts) >= 4 and parts[2].isdigit():
        return int(parts[2])
    return default


def unwrap_command(command: Optional[str]) -> Optional[str]:
    """Return a wrapper's payload, unwrapping nested wrappers.

    An older release escaped the wrapper and then wrapped it again, so a
    payload can itself be a wrapper; collapse those to the original command.
    """
    if not command:
        return command
    result = command
    seen = {command}
    while True:
        parts = managed_wrapper_parts(result)
        if not parts:
            return result
        decoded = _wrapper_payload(parts)
        if not decoded or decoded in seen:
            return result
        seen.add(decoded)
        result = decoded


def is_wrapped_command(command: Optional[str]) -> bool:
    return managed_wrapper_parts(command) is not None


def get_wrapped_command_origin(command: Optional[str]) -> Optional[str]:
    parts = managed_wrapper_parts(command)
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


def is_headless_prep_wrapped(command: Optional[str]) -> bool:
    return managed_wrapper_parts(command, get_headless_prep_script()) is not None


def unwrap_headless_prep_command(command: Optional[str]) -> Optional[str]:
    parts = managed_wrapper_parts(command, get_headless_prep_script())
    if not parts:
        return command
    decoded = _wrapper_payload(parts)
    return decoded if decoded else command
