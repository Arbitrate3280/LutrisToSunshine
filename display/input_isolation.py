"""Input isolation: udev rules, KWin integration, and Sunshine virtual device detection."""
import grp
import json
import os
import pwd
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from display.constants import (
    SUNSHINE_INPUT_PRODUCT_ID,
    SUNSHINE_INPUT_VENDOR_ID,
)
from display.state import DisplayState
from display.utils import run_command, safe_string


def sudo_prefix() -> Optional[List[str]]:
    if os.geteuid() == 0:
        return []
    if shutil.which("sudo"):
        return ["sudo"]
    if shutil.which("pkexec"):
        return ["pkexec"]
    return None


def run_privileged(command: List[str]) -> bool:
    prefix = sudo_prefix()
    if prefix is None:
        return False
    result = run_command(prefix + command)
    return result.returncode == 0


def reload_udev_rules() -> bool:
    prefix = sudo_prefix()
    if prefix is None:
        return False
    commands = [
        prefix + ["udevadm", "control", "--reload-rules"],
        prefix + ["udevadm", "trigger", "--subsystem-match=input"],
    ]
    for command in commands:
        result = run_command(command)
        if result.returncode != 0:
            return False
    return True


def current_user_name() -> str:
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return str(os.environ.get("USER") or "").strip()


def current_user_group() -> str:
    try:
        return grp.getgrgid(os.getgid()).gr_name
    except KeyError:
        return str(os.environ.get("USER") or "").strip()


def is_plasma_session() -> bool:
    current_desktop = safe_string(os.environ.get("XDG_CURRENT_DESKTOP")).lower()
    session_desktop = safe_string(os.environ.get("XDG_SESSION_DESKTOP")).lower()
    desktop_session = safe_string(os.environ.get("DESKTOP_SESSION")).lower()
    kde_full_session = safe_string(os.environ.get("KDE_FULL_SESSION")).lower()
    plasma_markers = ("kde", "plasma", "kwin")
    return (
        kde_full_session in {"1", "true", "yes", "on"}
        or any(marker in current_desktop for marker in plasma_markers)
        or any(marker in session_desktop for marker in plasma_markers)
        or any(marker in desktop_session for marker in plasma_markers)
    )


def host_session_name() -> str:
    parts = []
    for value in [
        safe_string(os.environ.get("XDG_CURRENT_DESKTOP")),
        safe_string(os.environ.get("XDG_SESSION_DESKTOP")),
        safe_string(os.environ.get("DESKTOP_SESSION")),
    ]:
        if value and value not in parts:
            parts.append(value)
    if parts:
        return " / ".join(parts)
    if safe_string(os.environ.get("KDE_FULL_SESSION")).lower() in {"1", "true", "yes", "on"}:
        return "KDE"
    return "unknown"


def input_isolation_mode() -> str:
    return "kwin-runtime-disable" if is_plasma_session() else "permissions-only"


def empty_kwin_input_isolation_status() -> Dict[str, Any]:
    return {
        "state": "inactive",
        "service": "",
        "disabled_devices": [],
        "failed_devices": [],
        "seen_device_count": 0,
        "last_error": "",
    }


def kwin_input_isolation_status(state: DisplayState) -> Dict[str, Any]:
    status = empty_kwin_input_isolation_status()
    path = Path(state.paths.kwin_input_isolation_status_file)
    if not path.exists():
        return status
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return status
    if isinstance(data, dict):
        status.update(data)
    if not isinstance(status.get("disabled_devices"), list):
        status["disabled_devices"] = []
    if not isinstance(status.get("failed_devices"), list):
        status["failed_devices"] = []
    status["state"] = safe_string(status.get("state")) or "inactive"
    status["service"] = safe_string(status.get("service"))
    status["last_error"] = safe_string(status.get("last_error"))
    try:
        status["seen_device_count"] = int(status.get("seen_device_count") or 0)
    except (TypeError, ValueError):
        status["seen_device_count"] = 0
    return status


def sunshine_virtual_input_devices() -> List[Dict[str, str]]:
    devices_path = Path("/proc/bus/input/devices")
    try:
        content = devices_path.read_text(encoding="utf-8")
    except OSError:
        return []

    devices: List[Dict[str, str]] = []
    current: Dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            vendor_id = safe_string(current.get("vendor_id")).lower()
            product_id = safe_string(current.get("product_id")).lower()
            if vendor_id == f"{SUNSHINE_INPUT_VENDOR_ID:04x}" and product_id == f"{SUNSHINE_INPUT_PRODUCT_ID:04x}":
                devices.append(
                    {
                        "name": safe_string(current.get("name")),
                        "event_path": safe_string(current.get("event_path")),
                    }
                )
            current = {}
            continue
        if line.startswith("I:"):
            for token in line[2:].strip().split():
                if "=" not in token:
                    continue
                key, value = token.split("=", 1)
                lowered = key.lower()
                if lowered == "vendor":
                    current["vendor_id"] = value
                elif lowered == "product":
                    current["product_id"] = value
        elif line.startswith('N: Name="') and line.endswith('"'):
            current["name"] = line[len('N: Name="'):-1]
        elif line.startswith("H: Handlers="):
            handlers = line[len("H: Handlers="):].split()
            event_name = next((item for item in handlers if item.startswith("event")), "")
            if event_name:
                current["event_path"] = f"/dev/input/{event_name}"

    if current:
        vendor_id = safe_string(current.get("vendor_id")).lower()
        product_id = safe_string(current.get("product_id")).lower()
        if vendor_id == f"{SUNSHINE_INPUT_VENDOR_ID:04x}" and product_id == f"{SUNSHINE_INPUT_PRODUCT_ID:04x}":
            devices.append(
                {
                    "name": safe_string(current.get("name")),
                    "event_path": safe_string(current.get("event_path")),
                }
            )
    return devices


def udev_rule(isolation_mode: Optional[str] = None) -> str:
    user_name = current_user_name()
    group_name = current_user_group()
    sunshine_input_permissions = [
        'MODE="0660"',
        'TAG+="uaccess"',
    ]
    if user_name:
        sunshine_input_permissions.append(f'OWNER="{user_name}"')
    if group_name:
        sunshine_input_permissions.append(f'GROUP="{group_name}"')
    sunshine_input_clause = ", ".join(sunshine_input_permissions)
    return f"""# Managed by LutrisToSunshine display.
ACTION=="add|change", SUBSYSTEM=="input", ATTRS{{id/vendor}}=="{SUNSHINE_INPUT_VENDOR_ID:04x}", ATTRS{{id/product}}=="{SUNSHINE_INPUT_PRODUCT_ID:04x}", {sunshine_input_clause}
"""


def install_udev_rule(state: DisplayState) -> bool:
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
        handle.write(udev_rule())
        temp_path = handle.name
    try:
        ok = run_privileged(["install", "-D", "-m", "0644", temp_path, state.udev_rule_path])
        if not ok:
            return False
        return reload_udev_rules()
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def remove_udev_rule(state: DisplayState) -> bool:
    ok = run_privileged(["rm", "-f", state.udev_rule_path])
    if not ok:
        return False
    return reload_udev_rules()


def clean_kde_libinput_config() -> None:
    vendor = f"{SUNSHINE_INPUT_VENDOR_ID:04x}"
    product = f"{SUNSHINE_INPUT_PRODUCT_ID:04x}"
    for path in [
        Path("~/.config/kcminputrc").expanduser(),
        Path("~/.config/kdedefaults/kcminputrc").expanduser(),
    ]:
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        except OSError:
            continue
        cleaned = []
        skip_next = False
        changed = False
        for line in lines:
            if skip_next:
                skip_next = False
                changed = True
                continue
            stripped = line.strip()
            # Match [Libinput][beef][dead][... passthrough]
            if stripped.startswith("[Libinput]") and f"[{vendor}][{product}]" in stripped:
                skip_next = True
                changed = True
                continue
            cleaned.append(line)
        if changed:
            try:
                path.write_text("".join(cleaned), encoding="utf-8")
            except OSError:
                pass
