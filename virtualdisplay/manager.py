import base64
import glob
import grp
import hashlib
import json
import os
import pwd
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.input import get_user_input


PROFILE_NAME = "default"
CONFIG_ROOT = Path("~/.config/lutristosunshine").expanduser()
VIRTUALDISPLAY_ROOT = CONFIG_ROOT / "virtualdisplay"
PROFILE_ROOT = VIRTUALDISPLAY_ROOT / PROFILE_NAME
BIN_ROOT = CONFIG_ROOT / "bin"
STATE_PATH = VIRTUALDISPLAY_ROOT / "virtualdisplay.json"
SUNSHINE_UNIT = "app-dev.lizardbyte.app.Sunshine.service"
LEGACY_SWAY_UNIT = "lutristosunshine-virtualdisplay-sway.service"
LEGACY_SUNSHINE_UNIT = "lutristosunshine-virtualdisplay-sunshine.service"
LEGACY_INPUT_BRIDGE_UNIT = "lutristosunshine-virtualdisplay-inputbridge.service"
LEGACY_AUDIO_GUARD_UNIT = "lutristosunshine-virtualdisplay-audioguard.service"
FLATPAK_PORTAL_UNIT = "flatpak-portal.service"
SWAYSOCK_PATH = f"/run/user/{os.getuid()}/lutristosunshine-virtualdisplay.sock"
WAYLAND_DISPLAY_PATH = PROFILE_ROOT / "wayland-display"
AUDIO_MODULE_PATH = PROFILE_ROOT / "audio-module-id"
PORTAL_LOCK_PATH = PROFILE_ROOT / "flatpak-portal.lock"
PORTAL_ACTIVE_PATH = PROFILE_ROOT / "flatpak-portal-active"
LAST_LAUNCH_LOG_PATH = PROFILE_ROOT / "last-launch.log"
INPUT_BRIDGE_STATUS_PATH = PROFILE_ROOT / "input-bridge-status.json"
UDEV_RULE_PATH = "/etc/udev/rules.d/85-lutristosunshine-sunshine-input.rules"
FALLBACK_WIDTH = 1920
FALLBACK_HEIGHT = 1080
FALLBACK_FPS = 60
SUNSHINE_FLATPAK_ID = "dev.lizardbyte.app.Sunshine"
SUNSHINE_INPUT_VENDOR_ID = 0xBEEF
SUNSHINE_INPUT_PRODUCT_ID = 0xDEAD
BRIDGE_DEVICE_PHYS_PREFIX = "lts-inputbridge/"
HIDRAW_BUFFER_MAX = 4096
AUDIO_GUARD_POLL_INTERVAL_SECONDS = 0.5
SUNSHINE_OWNED_SINK_NAMES = [
    "sink-sunshine-stereo",
    "sink-sunshine-surround51",
    "sink-sunshine-surround71",
]
FLATPAK_SPAWN_HOST_PREFIX = ["flatpak-spawn", "--host"]
FLATPAK_FLAG_OPTIONS = {
    "-d",
    "-p",
    "--a11y-bus",
    "--clear-env",
    "--devel",
    "--die-with-parent",
    "--file-forwarding",
    "--log-a11y-bus",
    "--log-session-bus",
    "--log-system-bus",
    "--no-a11y-bus",
    "--no-documents-portal",
    "--no-session-bus",
    "--parent-expose-pids",
    "--parent-share-pids",
    "--sandbox",
    "--session-bus",
    "--system",
    "--user",
}
FLATPAK_VALUE_OPTIONS = {
    "--a11y-own-name",
    "--add-policy",
    "--allow",
    "--allow-if",
    "--app-path",
    "--arch",
    "--branch",
    "--command",
    "--commit",
    "--cwd",
    "--device",
    "--device-if",
    "--disallow",
    "--env",
    "--env-fd",
    "--filesystem",
    "--instance-id-fd",
    "--installation",
    "--no-talk-name",
    "--nodevice",
    "--nofilesystem",
    "--nousb",
    "--own-name",
    "--parent-pid",
    "--persist",
    "--remove-policy",
    "--runtime",
    "--runtime-commit",
    "--runtime-version",
    "--share",
    "--share-if",
    "--socket",
    "--socket-if",
    "--system-no-talk-name",
    "--system-own-name",
    "--system-talk-name",
    "--talk-name",
    "--unshare",
    "--unset-env",
    "--usb",
    "--usb-list",
    "--usb-list-file",
    "--usr-path",
}
FLATPAK_VALUE_PREFIXES = tuple(f"{option}=" for option in FLATPAK_VALUE_OPTIONS)
VIRTUALDISPLAY_SANDBOX_UNSET_VARS = [
    "DESKTOP_STARTUP_ID",
    "KDE_FULL_SESSION",
    "KDE_SESSION_UID",
    "KDE_SESSION_VERSION",
    "KONSOLE_DBUS_ACTIVATION_COOKIE",
    "KONSOLE_DBUS_SERVICE",
    "KONSOLE_DBUS_SESSION",
    "KONSOLE_DBUS_WINDOW",
    "SESSION_MANAGER",
    "WINDOWID",
    "XDG_ACTIVATION_TOKEN",
    "XDG_MENU_PREFIX",
]
FLATPAK_PORTAL_ENV_KEYS = [
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "SWAYSOCK",
    "DESKTOP_SESSION",
    "XDG_CURRENT_DESKTOP",
    "XDG_SESSION_DESKTOP",
    "XDG_SESSION_TYPE",
    "PULSE_SINK",
]
FLATPAK_PORTAL_SWITCH_TIMEOUT = 20
FLATPAK_PORTAL_SPAWN_TIMEOUT = 15
FLATPAK_PORTAL_RESTORE_GRACE = 2
GAMEPAD_BUTTON_CODES = {
    304, 305, 307, 308, 310, 311, 312, 313, 314, 315, 316, 317, 318,
    544, 545, 546, 547,
}
GAMEPAD_ABS_CODES = {0, 1, 2, 3, 4, 5, 16, 17}


def _config_root_candidates() -> List[Path]:
    return [
        Path("~/.config/sunshine").expanduser(),
        Path("~/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine").expanduser(),
    ]


def detect_sunshine_config_root() -> Path:
    for candidate in _config_root_candidates():
        if candidate.exists():
            return candidate
    return _config_root_candidates()[0]


def _sunshine_binary() -> Optional[str]:
    binary = shutil.which("sunshine")
    if binary:
        return binary
    flatpak = shutil.which("flatpak")
    if not flatpak:
        return None
    result = subprocess.run(
        [flatpak, "info", SUNSHINE_FLATPAK_ID],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode == 0:
        return f"{flatpak} run {SUNSHINE_FLATPAK_ID}"
    return None


def _evdev_import_error() -> Optional[str]:
    try:
        from evdev import InputDevice, ecodes  # noqa: F401
    except ImportError:
        return (
            "Missing Python module: evdev. "
            f"Install it in the active interpreter with '{sys.executable} -m pip install evdev' "
            "or rerun 'pip install -r requirements.txt' inside your virtualenv."
        )
    return None


def _safe_string(value: Any) -> str:
    return str(value or "").strip()


def _preferred_event_symlink(event_path: str, directory: str) -> str:
    candidates = []
    for candidate in glob.glob(os.path.join(directory, "*")):
        try:
            if os.path.realpath(candidate) != os.path.realpath(event_path):
                continue
        except OSError:
            continue
        name = os.path.basename(candidate)
        if "event" not in name:
            continue
        candidates.append(candidate)
    candidates.sort(key=lambda item: (0 if "-event" in os.path.basename(item) else 1, len(item), item))
    return candidates[0] if candidates else ""


def _selection_id_from_fingerprint(fingerprint: Dict[str, Any]) -> str:
    payload = json.dumps(
        {
            "by_id": _safe_string(fingerprint.get("by_id")),
            "uniq": _safe_string(fingerprint.get("uniq")),
            "phys": _safe_string(fingerprint.get("phys")),
            "vendor_id": _safe_string(fingerprint.get("vendor_id")).lower(),
            "product_id": _safe_string(fingerprint.get("product_id")).lower(),
            "name": _safe_string(fingerprint.get("name")),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def _normalized_selection_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    fingerprint = entry.get("fingerprint", {}) if isinstance(entry, dict) else {}
    if not isinstance(fingerprint, dict):
        fingerprint = {}
    normalized_fingerprint = {
        "by_id": _safe_string(fingerprint.get("by_id")),
        "uniq": _safe_string(fingerprint.get("uniq")),
        "phys": _safe_string(fingerprint.get("phys")),
        "vendor_id": _safe_string(fingerprint.get("vendor_id")).lower(),
        "product_id": _safe_string(fingerprint.get("product_id")).lower(),
        "name": _safe_string(fingerprint.get("name")),
    }
    selection_id = _safe_string(entry.get("selection_id")) if isinstance(entry, dict) else ""
    if not selection_id:
        selection_id = _selection_id_from_fingerprint(normalized_fingerprint)
    label = _safe_string(entry.get("label")) if isinstance(entry, dict) else ""
    if not label:
        label = normalized_fingerprint["name"] or selection_id
    return {
        "selection_id": selection_id,
        "label": label,
        "fingerprint": normalized_fingerprint,
    }


def _empty_exclusive_input_state() -> Dict[str, Any]:
    return {"devices": []}


def _normalized_exclusive_input_state(raw: Any) -> Dict[str, Any]:
    devices = []
    if isinstance(raw, dict):
        raw_devices = raw.get("devices", [])
        if isinstance(raw_devices, list):
            devices = [_normalized_selection_entry(item) for item in raw_devices if isinstance(item, dict)]
    return {"devices": devices}


def _has_selected_input_devices(state: Dict[str, Any]) -> bool:
    return bool(state.get("exclusive_input_devices", {}).get("devices"))


def _selection_matches_device(selection: Dict[str, Any], device_info: Dict[str, Any]) -> bool:
    fingerprint = selection.get("fingerprint", {})
    device_fingerprint = device_info.get("fingerprint", {})
    by_id = _safe_string(fingerprint.get("by_id"))
    if by_id and by_id == _safe_string(device_fingerprint.get("by_id")):
        return True
    uniq = _safe_string(fingerprint.get("uniq"))
    if uniq and uniq == _safe_string(device_fingerprint.get("uniq")):
        return True
    phys = _safe_string(fingerprint.get("phys"))
    if (
        phys
        and phys == _safe_string(device_fingerprint.get("phys"))
        and _safe_string(fingerprint.get("vendor_id")) == _safe_string(device_fingerprint.get("vendor_id"))
        and _safe_string(fingerprint.get("product_id")) == _safe_string(device_fingerprint.get("product_id"))
        and _safe_string(fingerprint.get("name")) == _safe_string(device_fingerprint.get("name"))
    ):
        return True
    return False


def _device_matches_any_selection(device_info: Dict[str, Any], selections: List[Dict[str, Any]]) -> bool:
    return any(_selection_matches_device(selection, device_info) for selection in selections)


def _selection_runtime_identity_bits(runtime: Dict[str, Any]) -> List[str]:
    identity_bits = []
    source_name = _safe_string(runtime.get("source_name"))
    source_vendor = _safe_string(runtime.get("source_vendor"))
    source_product = _safe_string(runtime.get("source_product"))
    bridge_mode = _safe_string(runtime.get("bridge_mode"))
    hidraw_path = _safe_string(runtime.get("hidraw_path"))
    hid_output_forwarding = bool(runtime.get("hid_output_forwarding"))
    virtual_event_path = _safe_string(runtime.get("virtual_event_path"))
    virtual_hidraw_path = _safe_string(runtime.get("virtual_hidraw_path"))
    if source_name:
        identity_bits.append(source_name)
    if source_vendor and source_product:
        identity_bits.append(f"{source_vendor}:{source_product}")
    if bridge_mode:
        identity_bits.append(bridge_mode)
    if hidraw_path:
        identity_bits.append(os.path.basename(hidraw_path))
    if virtual_event_path:
        identity_bits.append(f"virt:{os.path.basename(virtual_event_path)}")
    if virtual_hidraw_path:
        identity_bits.append(f"virt:{os.path.basename(virtual_hidraw_path)}")
    if hid_output_forwarding:
        identity_bits.append("hid output")
    if bridge_mode == "uhid":
        output_count = int(runtime.get("uhid_output_count", 0) or 0)
        get_count = int(runtime.get("uhid_get_report_count", 0) or 0)
        set_count = int(runtime.get("uhid_set_report_count", 0) or 0)
        open_count = int(runtime.get("uhid_open_count", 0) or 0)
        close_count = int(runtime.get("uhid_close_count", 0) or 0)
        identity_bits.append(f"o/g/s {output_count}/{get_count}/{set_count}")
        identity_bits.append(f"open/close {open_count}/{close_count}")
        last_uhid_event = _safe_string(runtime.get("last_uhid_event"))
        if last_uhid_event:
            identity_bits.append(f"last:{last_uhid_event}")
    elif bridge_mode == "uinput-fallback":
        upload_count = int(runtime.get("ff_upload_count", 0) or 0)
        play_count = int(runtime.get("ff_play_count", 0) or 0)
        erase_count = int(runtime.get("ff_erase_count", 0) or 0)
        identity_bits.append(f"ff u/p/e {upload_count}/{play_count}/{erase_count}")
    ff_label = "rumble enabled" if runtime.get("ff_supported") else "no rumble"
    identity_bits.append(ff_label)
    return identity_bits


def _controller_label(name: str, by_id: str, phys: str) -> str:
    if by_id:
        return f"{name} [{os.path.basename(by_id)}]"
    if phys:
        return f"{name} [{phys}]"
    return name


def _is_bridge_input_phys(phys: str) -> bool:
    return _safe_string(phys).startswith(BRIDGE_DEVICE_PHYS_PREFIX)


def _runtime_bridge_node_paths(state: Optional[Dict[str, Any]] = None) -> Dict[str, set[str]]:
    if state is None:
        state = load_state()
    runtime_status = _input_bridge_status(state)
    paths = {
        "virtual_event_paths": set(),
        "virtual_hidraw_paths": set(),
    }
    for item in runtime_status.get("devices", []):
        if not isinstance(item, dict):
            continue
        for key, bucket in (
            ("virtual_event_path", "virtual_event_paths"),
            ("virtual_hidraw_path", "virtual_hidraw_paths"),
        ):
            path = _safe_string(item.get(key))
            if not path:
                continue
            try:
                paths[bucket].add(os.path.realpath(path))
            except OSError:
                continue
    return paths


def _list_controller_devices() -> Tuple[List[Dict[str, Any]], Optional[str]]:
    error = _evdev_import_error()
    if error:
        return [], error

    from evdev import InputDevice, ecodes

    runtime_paths = _runtime_bridge_node_paths()
    devices: List[Dict[str, Any]] = []
    for event_path in sorted(glob.glob("/dev/input/event*")):
        try:
            device = InputDevice(event_path)
            capabilities = device.capabilities(absinfo=False)
        except OSError:
            continue

        vendor_id = f"{device.info.vendor:04x}"
        product_id = f"{device.info.product:04x}"
        if (device.info.vendor, device.info.product) == (
            SUNSHINE_INPUT_VENDOR_ID,
            SUNSHINE_INPUT_PRODUCT_ID,
        ):
            device.close()
            continue
        try:
            resolved_event_path = os.path.realpath(event_path)
        except OSError:
            resolved_event_path = event_path
        if resolved_event_path in runtime_paths["virtual_event_paths"]:
            device.close()
            continue
        if _is_bridge_input_phys(_safe_string(device.phys)):
            device.close()
            continue

        keys = set(capabilities.get(ecodes.EV_KEY, []))
        abs_axes = set(capabilities.get(ecodes.EV_ABS, []))
        if not (keys & GAMEPAD_BUTTON_CODES) and not (abs_axes & GAMEPAD_ABS_CODES):
            device.close()
            continue

        name = _safe_string(device.name) or os.path.basename(event_path)
        by_id = _preferred_event_symlink(event_path, "/dev/input/by-id")
        phys = _safe_string(device.phys)
        fingerprint = {
            "by_id": by_id,
            "uniq": _safe_string(device.uniq),
            "phys": phys,
            "vendor_id": vendor_id,
            "product_id": product_id,
            "name": name,
        }
        devices.append(
            {
                "event_path": event_path,
                "label": _controller_label(name, by_id, phys),
                "fingerprint": fingerprint,
                "selection_id": _selection_id_from_fingerprint(fingerprint),
            }
        )
        device.close()

    devices.sort(key=lambda item: (item["label"].lower(), item["event_path"]))
    return devices, None


def _input_bridge_status(state: Dict[str, Any]) -> Dict[str, Any]:
    status_path = Path(state["paths"]["input_bridge_status_file"])
    if not status_path.exists():
        return {}
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _state_paths() -> Dict[str, str]:
    systemd_user_dir = Path("~/.config/systemd/user").expanduser()
    override_dir = systemd_user_dir / f"{SUNSHINE_UNIT}.d"
    return {
        "profile_root": str(PROFILE_ROOT),
        "bin_root": str(BIN_ROOT),
        "state_path": str(STATE_PATH),
        "sway_config": str(PROFILE_ROOT / "sway.conf"),
        "sway_start_script": str(BIN_ROOT / "lutristosunshine-start-headless-sway.sh"),
        "sunshine_start_script": str(BIN_ROOT / "lutristosunshine-start-virtualdisplay-sunshine.sh"),
        "sunshine_wrapper_script": str(BIN_ROOT / "lutristosunshine-run-virtualdisplay-service.sh"),
        "audio_create_script": str(BIN_ROOT / "lutristosunshine-create-audio-sink.sh"),
        "audio_cleanup_script": str(BIN_ROOT / "lutristosunshine-cleanup-audio-sink.sh"),
        "audio_guard_script": str(BIN_ROOT / "lutristosunshine-guard-audio-defaults.sh"),
        "launch_app_script": str(BIN_ROOT / "lutristosunshine-launch-app.sh"),
        "set_resolution_script": str(BIN_ROOT / "lutristosunshine-set-resolution.sh"),
        "reset_resolution_script": str(BIN_ROOT / "lutristosunshine-reset-resolution.sh"),
        "portal_lock_file": str(PORTAL_LOCK_PATH),
        "portal_active_file": str(PORTAL_ACTIVE_PATH),
        "last_launch_log_file": str(LAST_LAUNCH_LOG_PATH),
        "input_bridge_status_file": str(INPUT_BRIDGE_STATUS_PATH),
        "wayland_display_file": str(WAYLAND_DISPLAY_PATH),
        "audio_module_file": str(AUDIO_MODULE_PATH),
        "systemd_user_dir": str(systemd_user_dir),
        "sunshine_override_dir": str(override_dir),
        "sunshine_override": str(override_dir / "override.conf"),
        "legacy_sway_unit": str(systemd_user_dir / LEGACY_SWAY_UNIT),
        "legacy_sunshine_unit": str(systemd_user_dir / LEGACY_SUNSHINE_UNIT),
        "legacy_input_bridge_unit": str(systemd_user_dir / LEGACY_INPUT_BRIDGE_UNIT),
        "legacy_audio_guard_unit": str(systemd_user_dir / LEGACY_AUDIO_GUARD_UNIT),
        "input_bridge_script": str(BIN_ROOT / "lutristosunshine-input-bridge.py"),
        "sunshine_conf": str(detect_sunshine_config_root() / "sunshine.conf"),
    }


def _default_state() -> Dict[str, Any]:
    paths = _state_paths()
    return {
        "enabled": False,
        "profile": PROFILE_NAME,
        "audio_sink": "lts-sunshine-stereo",
        "host_audio_defaults": {"sink": "", "source": ""},
        "sway_socket": SWAYSOCK_PATH,
        "udev_rule_path": UDEV_RULE_PATH,
        "sunshine_audio_sink": None,
        "exclusive_input_devices": _empty_exclusive_input_state(),
        "paths": paths,
    }


def load_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return _default_state()
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_state()
    state = _default_state()
    state.update(data)
    state["exclusive_input_devices"] = _normalized_exclusive_input_state(
        state.get("exclusive_input_devices")
    )
    state["paths"] = _state_paths()
    return state


def save_state(state: Dict[str, Any]) -> None:
    VIRTUALDISPLAY_ROOT.mkdir(parents=True, exist_ok=True)
    state["exclusive_input_devices"] = _normalized_exclusive_input_state(
        state.get("exclusive_input_devices")
    )
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def is_enabled() -> bool:
    return bool(load_state().get("enabled"))


def get_app_prep_commands() -> List[Dict[str, str]]:
    state = load_state()
    if not state.get("enabled"):
        return []
    paths = state["paths"]
    return [
        {
            "do": paths["set_resolution_script"],
            "undo": paths["reset_resolution_script"],
        }
    ]


def get_launch_app_script() -> str:
    return load_state()["paths"]["launch_app_script"]


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


def _run(
    command: List[str],
    *,
    capture_output: bool = True,
    check: bool = False,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        text=True,
        capture_output=capture_output,
        check=check,
        env=env,
    )


def _parse_flatpak_run_command(command: str) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        return None, f"Unable to parse command: {exc}"

    if not tokens:
        return None, "Missing command."

    outer_prefix: List[str] = []
    index = 0
    if tokens[: len(FLATPAK_SPAWN_HOST_PREFIX)] == FLATPAK_SPAWN_HOST_PREFIX:
        outer_prefix = list(FLATPAK_SPAWN_HOST_PREFIX)
        index = len(FLATPAK_SPAWN_HOST_PREFIX)

    if tokens[index : index + 2] != ["flatpak", "run"]:
        return None, None
    index += 2

    flatpak_options: List[str] = []
    command_name: Optional[str] = None

    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return None, "Unsupported flatpak separator '--'."
        if not token.startswith("-"):
            app_id = token
            return {
                "outer_prefix": outer_prefix,
                "flatpak_options": flatpak_options,
                "command_name": command_name,
                "app_id": app_id,
                "app_args": tokens[index + 1 :],
            }, None
        if token in FLATPAK_FLAG_OPTIONS:
            flatpak_options.append(token)
            index += 1
            continue
        if token in FLATPAK_VALUE_OPTIONS:
            if index + 1 >= len(tokens):
                return None, f"Missing value for flatpak option: {token}"
            value = tokens[index + 1]
            if token == "--command":
                command_name = value
            else:
                flatpak_options.extend([token, value])
            index += 2
            continue

        matched_prefix = next(
            (prefix for prefix in FLATPAK_VALUE_PREFIXES if token.startswith(prefix)),
            None,
        )
        if matched_prefix:
            if matched_prefix == "--command=":
                command_name = token.split("=", 1)[1]
            else:
                flatpak_options.append(token)
            index += 1
            continue

        return None, f"Unsupported flatpak option: {token}"

    return None, "Missing Flatpak application ID."


def _resolve_flatpak_default_command(parsed_command: Dict[str, Any]) -> Optional[str]:
    lookup_command = [*parsed_command["outer_prefix"], "flatpak", "info", "--show-metadata", parsed_command["app_id"]]
    result = _run(lookup_command, check=False)
    if result.returncode != 0:
        return None
    for line in (result.stdout or "").splitlines():
        if line.startswith("command="):
            command_name = line.split("=", 1)[1].strip()
            if command_name:
                return command_name
    return None


def analyze_flatpak_command_for_virtualdisplay(command: Optional[str]) -> Optional[str]:
    if not command:
        return None
    parsed_command, error = _parse_flatpak_run_command(command)
    if parsed_command is None:
        return error
    return None


def _systemctl_user(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return _run(["systemctl", "--user", *args], check=check)


def _sudo_prefix() -> Optional[List[str]]:
    if os.geteuid() == 0:
        return []
    if shutil.which("sudo"):
        return ["sudo"]
    if shutil.which("pkexec"):
        return ["pkexec"]
    return None


def _run_privileged(command: List[str]) -> bool:
    prefix = _sudo_prefix()
    if prefix is None:
        return False
    result = _run(prefix + command)
    return result.returncode == 0


def _reload_udev_rules() -> bool:
    prefix = _sudo_prefix()
    if prefix is None:
        return False
    commands = [
        prefix + ["udevadm", "control", "--reload-rules"],
        prefix + ["udevadm", "trigger", "--subsystem-match=input"],
    ]
    for command in commands:
        result = _run(command)
        if result.returncode != 0:
            return False
    return True


def _read_key_value(path: Path, key: str) -> Dict[str, Any]:
    if not path.exists():
        return {"present": False, "value": ""}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        current_key, value = stripped.split("=", 1)
        if current_key.strip() == key:
            return {"present": True, "value": value.strip()}
    return {"present": False, "value": ""}


def _set_key_value(path: Path, key: str, value: str) -> None:
    lines: List[str] = []
    found = False
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()

    updated_lines: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            current_key = stripped.split("=", 1)[0].strip()
            if current_key == key:
                updated_lines.append(f"{key} = {value}")
                found = True
                continue
        updated_lines.append(line)

    if not found:
        if updated_lines and updated_lines[-1] != "":
            updated_lines.append("")
        updated_lines.append(f"{key} = {value}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")


def _remove_key(path: Path, key: str) -> None:
    if not path.exists():
        return
    updated_lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            current_key = stripped.split("=", 1)[0].strip()
            if current_key == key:
                continue
        updated_lines.append(line)
    path.write_text("\n".join(updated_lines).rstrip() + "\n", encoding="utf-8")


def _sunshine_audio_capture_target(state: Dict[str, Any]) -> str:
    sink_name = str(state.get("audio_sink") or "").strip()
    return sink_name


def _managed_audio_sink_names(state: Dict[str, Any]) -> List[str]:
    names = list(SUNSHINE_OWNED_SINK_NAMES)
    sink_name = str(state.get("audio_sink") or "").strip()
    if sink_name:
        names.insert(0, sink_name)
    return list(dict.fromkeys(names))


def _managed_audio_source_names(state: Dict[str, Any]) -> List[str]:
    return [f"{sink_name}.monitor" for sink_name in _managed_audio_sink_names(state)]


def _remember_sunshine_audio_sink(state: Dict[str, Any]) -> None:
    if state.get("sunshine_audio_sink") is None:
        sunshine_conf = Path(state["paths"]["sunshine_conf"])
        state["sunshine_audio_sink"] = _read_key_value(sunshine_conf, "audio_sink")


def _set_runtime_sunshine_audio_sink(state: Dict[str, Any]) -> None:
    sunshine_conf = Path(state["paths"]["sunshine_conf"])
    _remember_sunshine_audio_sink(state)
    capture_target = _sunshine_audio_capture_target(state)
    if capture_target:
        _set_key_value(sunshine_conf, "audio_sink", capture_target)


def _pactl_info_value(key: str) -> str:
    result = subprocess.run(
        ["pactl", "info"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    prefix = f"{key}:"
    for line in result.stdout.splitlines():
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip()
    return ""


def _snapshot_host_audio_defaults(state: Dict[str, Any]) -> None:
    current_sink = _pactl_info_value("Default Sink")
    current_source = _pactl_info_value("Default Source")
    existing_defaults = state.get("host_audio_defaults") or {}

    if (
        current_sink in _managed_audio_sink_names(state)
        and current_source in _managed_audio_source_names(state)
        and existing_defaults.get("sink")
        and existing_defaults.get("source")
    ):
        return

    state["host_audio_defaults"] = {
        "sink": current_sink,
        "source": current_source,
    }


def _restore_host_audio_defaults(state: Dict[str, Any]) -> None:
    defaults = state.get("host_audio_defaults") or {}
    sink_name = str(defaults.get("sink") or "").strip()
    source_name = str(defaults.get("source") or "").strip()

    if sink_name:
        subprocess.run(
            ["pactl", "set-default-sink", sink_name],
            text=True,
            capture_output=True,
            check=False,
        )
    if source_name:
        subprocess.run(
            ["pactl", "set-default-source", source_name],
            text=True,
            capture_output=True,
            check=False,
        )
    state["host_audio_defaults"] = {"sink": "", "source": ""}


def _write_file(path: Path, content: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


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


def _input_bridge_script(state: Dict[str, Any]) -> str:
    paths = state["paths"]
    python_executable = sys.executable or "/usr/bin/env python3"
    return f"""#!{python_executable}
import ctypes
import errno
import fcntl
import glob
import grp
import json
import logging
import math
import os
import pwd
import select
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from evdev import InputDevice, UInput, ecodes

STATE_PATH = Path({paths["state_path"]!r})
STATUS_PATH = Path({paths["input_bridge_status_file"]!r})
SUNSHINE_INPUT_ID = ({SUNSHINE_INPUT_VENDOR_ID}, {SUNSHINE_INPUT_PRODUCT_ID})
BRIDGE_DEVICE_PHYS_PREFIX = {BRIDGE_DEVICE_PHYS_PREFIX!r}
GAMEPAD_BUTTON_CODES = {sorted(GAMEPAD_BUTTON_CODES)!r}
GAMEPAD_ABS_CODES = {sorted(GAMEPAD_ABS_CODES)!r}
HIDRAW_BUFFER_MAX = {HIDRAW_BUFFER_MAX}
UHID_DATA_MAX = 4096
UHID_DESTROY = 1
UHID_START = 2
UHID_STOP = 3
UHID_OPEN = 4
UHID_CLOSE = 5
UHID_OUTPUT = 6
UHID_GET_REPORT = 9
UHID_GET_REPORT_REPLY = 10
UHID_CREATE2 = 11
UHID_INPUT2 = 12
UHID_SET_REPORT = 13
UHID_SET_REPORT_REPLY = 14
UHID_FEATURE_REPORT = 0
UHID_OUTPUT_REPORT = 1
UHID_INPUT_REPORT = 2
STOP_EVENT = threading.Event()
STATUS_LOCK = threading.Lock()
CLAIMS_LOCK = threading.Lock()
CLAIMED_PATHS = set()
RUNTIME_STATUS = {{}}

logging.basicConfig(level=logging.INFO, format="[LTS Input Bridge] %(message)s")
LOGGER = logging.getLogger("lutristosunshine-inputbridge")


IOC_NRBITS = 8
IOC_TYPEBITS = 8
IOC_SIZEBITS = 14
IOC_DIRBITS = 2
IOC_NRSHIFT = 0
IOC_TYPESHIFT = IOC_NRSHIFT + IOC_NRBITS
IOC_SIZESHIFT = IOC_TYPESHIFT + IOC_TYPEBITS
IOC_DIRSHIFT = IOC_SIZESHIFT + IOC_SIZEBITS
IOC_NONE = 0
IOC_WRITE = 1
IOC_READ = 2


def _IOC(direction, ioc_type, number, size):
    return (
        (direction << IOC_DIRSHIFT)
        | (ioc_type << IOC_TYPESHIFT)
        | (number << IOC_NRSHIFT)
        | (size << IOC_SIZESHIFT)
    )


def _IOR(ioc_type, number, size):
    return _IOC(IOC_READ, ioc_type, number, size)


def _IOW(ioc_type, number, size):
    return _IOC(IOC_WRITE, ioc_type, number, size)


def _IOWR(ioc_type, number, size):
    return _IOC(IOC_READ | IOC_WRITE, ioc_type, number, size)


def safe_string(value):
    return str(value or "").strip()


def is_bridge_phys(phys):
    return safe_string(phys).startswith(BRIDGE_DEVICE_PHYS_PREFIX)


def current_user_name():
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return safe_string(os.environ.get("USER"))


def current_user_group():
    try:
        return grp.getgrgid(os.getgid()).gr_name
    except KeyError:
        return safe_string(os.environ.get("USER"))


class UHIDCreate2Req(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("name", ctypes.c_uint8 * 128),
        ("phys", ctypes.c_uint8 * 64),
        ("uniq", ctypes.c_uint8 * 64),
        ("rd_size", ctypes.c_uint16),
        ("bus", ctypes.c_uint16),
        ("vendor", ctypes.c_uint32),
        ("product", ctypes.c_uint32),
        ("version", ctypes.c_uint32),
        ("country", ctypes.c_uint32),
        ("rd_data", ctypes.c_uint8 * HIDRAW_BUFFER_MAX),
    ]


class UHIDInput2Req(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("size", ctypes.c_uint16),
        ("data", ctypes.c_uint8 * UHID_DATA_MAX),
    ]


class UHIDOutputReq(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("data", ctypes.c_uint8 * UHID_DATA_MAX),
        ("size", ctypes.c_uint16),
        ("rtype", ctypes.c_uint8),
    ]


class UHIDGetReportReq(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("id", ctypes.c_uint32),
        ("rnum", ctypes.c_uint8),
        ("rtype", ctypes.c_uint8),
    ]


class UHIDGetReportReplyReq(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("id", ctypes.c_uint32),
        ("err", ctypes.c_uint16),
        ("size", ctypes.c_uint16),
        ("data", ctypes.c_uint8 * UHID_DATA_MAX),
    ]


class UHIDSetReportReq(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("id", ctypes.c_uint32),
        ("rnum", ctypes.c_uint8),
        ("rtype", ctypes.c_uint8),
        ("size", ctypes.c_uint16),
        ("data", ctypes.c_uint8 * UHID_DATA_MAX),
    ]


class UHIDSetReportReplyReq(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("id", ctypes.c_uint32),
        ("err", ctypes.c_uint16),
    ]


class UHIDStartReq(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("dev_flags", ctypes.c_uint64)]


class UHIDEventUnion(ctypes.Union):
    _fields_ = [
        ("create2", UHIDCreate2Req),
        ("input2", UHIDInput2Req),
        ("output", UHIDOutputReq),
        ("get_report", UHIDGetReportReq),
        ("get_report_reply", UHIDGetReportReplyReq),
        ("set_report", UHIDSetReportReq),
        ("set_report_reply", UHIDSetReportReplyReq),
        ("start", UHIDStartReq),
    ]


class UHIDEvent(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("u", UHIDEventUnion),
    ]


HIDRAW_REPORT_DESCRIPTOR_SIZE = ctypes.c_uint32


def hidraw_report_descriptor_size_type():
    return ctypes.c_uint32


def hidraw_report_type_from_uhid(rtype):
    if rtype == UHID_FEATURE_REPORT:
        return "feature"
    if rtype == UHID_OUTPUT_REPORT:
        return "output"
    if rtype == UHID_INPUT_REPORT:
        return "input"
    return "output"


def _copy_bytes(target, data):
    if isinstance(data, str):
        data = data.encode("utf-8", "replace")
    encoded = bytes(data[: max(0, len(target) - 1)])
    for index, value in enumerate(encoded):
        target[index] = value
    if len(target):
        target[min(len(encoded), len(target) - 1)] = 0


def preferred_event_symlink(event_path):
    candidates = []
    for candidate in glob.glob("/dev/input/by-id/*"):
        try:
            if os.path.realpath(candidate) != os.path.realpath(event_path):
                continue
        except OSError:
            continue
        if "event" not in os.path.basename(candidate):
            continue
        candidates.append(candidate)
    candidates.sort(key=lambda item: (0 if "-event" in os.path.basename(item) else 1, len(item), item))
    return candidates[0] if candidates else ""


def normalized_selection(entry):
    fingerprint = entry.get("fingerprint", {{}}) if isinstance(entry, dict) else {{}}
    if not isinstance(fingerprint, dict):
        fingerprint = {{}}
    normalized = {{
        "selection_id": safe_string(entry.get("selection_id")) or "unknown",
        "label": safe_string(entry.get("label")) or safe_string(fingerprint.get("name")) or "Unknown controller",
        "fingerprint": {{
            "by_id": safe_string(fingerprint.get("by_id")),
            "uniq": safe_string(fingerprint.get("uniq")),
            "phys": safe_string(fingerprint.get("phys")),
            "vendor_id": safe_string(fingerprint.get("vendor_id")).lower(),
            "product_id": safe_string(fingerprint.get("product_id")).lower(),
            "name": safe_string(fingerprint.get("name")),
        }},
    }}
    return normalized


def load_selections():
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    raw = payload.get("exclusive_input_devices", {{}})
    if not isinstance(raw, dict):
        return []
    raw_devices = raw.get("devices", [])
    if not isinstance(raw_devices, list):
        return []
    return [normalized_selection(item) for item in raw_devices if isinstance(item, dict)]


def selection_matches(selection, device_info):
    fingerprint = selection.get("fingerprint", {{}})
    device_fingerprint = device_info.get("fingerprint", {{}})
    by_id = safe_string(fingerprint.get("by_id"))
    if by_id and by_id == safe_string(device_fingerprint.get("by_id")):
        return True
    uniq = safe_string(fingerprint.get("uniq"))
    if uniq and uniq == safe_string(device_fingerprint.get("uniq")):
        return True
    phys = safe_string(fingerprint.get("phys"))
    if (
        phys
        and phys == safe_string(device_fingerprint.get("phys"))
        and safe_string(fingerprint.get("vendor_id")) == safe_string(device_fingerprint.get("vendor_id"))
        and safe_string(fingerprint.get("product_id")) == safe_string(device_fingerprint.get("product_id"))
        and safe_string(fingerprint.get("name")) == safe_string(device_fingerprint.get("name"))
    ):
        return True
    return False


def read_int_file(path):
    try:
        return int(Path(path).read_text(encoding="utf-8").strip(), 0)
    except (OSError, ValueError):
        return 0


def parse_report_descriptor(report_descriptor):
    report_size = 0
    report_count = 0
    report_id = 0
    totals = {{
        "input": {{}},
        "output": {{}},
        "feature": {{}},
    }}
    index = 0
    while index < len(report_descriptor):
        prefix = report_descriptor[index]
        index += 1
        if prefix == 0xFE:
            if index + 1 >= len(report_descriptor):
                break
            data_size = report_descriptor[index]
            index += 2 + data_size
            continue
        size_code = prefix & 0x3
        data_size = 4 if size_code == 3 else size_code
        item_type = (prefix >> 2) & 0x3
        item_tag = (prefix >> 4) & 0xF
        data = report_descriptor[index : index + data_size]
        index += data_size
        value = int.from_bytes(data, "little", signed=False) if data else 0

        if item_type == 1:
            if item_tag == 7:
                report_size = value
            elif item_tag == 8:
                report_id = value
            elif item_tag == 9:
                report_count = value
            continue

        if item_type != 0:
            continue

        kind = None
        if item_tag == 8:
            kind = "input"
        elif item_tag == 9:
            kind = "output"
        elif item_tag == 11:
            kind = "feature"

        if not kind:
            continue

        totals[kind][report_id] = totals[kind].get(report_id, 0) + (report_size * report_count)

    lengths = {{}}
    for kind, values in totals.items():
        lengths[kind] = {{}}
        for report_num, total_bits in values.items():
            report_len = int(math.ceil(total_bits / 8.0))
            if report_num:
                report_len += 1
            lengths[kind][report_num] = max(1, report_len)
    return lengths


def discover_hid_details(event_path):
    event_name = os.path.basename(event_path)
    input_sys = os.path.realpath(os.path.join("/sys/class/input", event_name, "device"))
    if not input_sys:
        return None
    hid_parent_link = os.path.join(input_sys, "device")
    if not os.path.exists(hid_parent_link):
        return None
    hid_parent = os.path.realpath(hid_parent_link)
    descriptor_path = os.path.join(hid_parent, "report_descriptor")
    if not os.path.exists(descriptor_path):
        return None
    hidraw_candidates = sorted(glob.glob(os.path.join(hid_parent, "hidraw", "hidraw*")))
    if not hidraw_candidates:
        return None
    try:
        report_descriptor = Path(descriptor_path).read_bytes()
    except OSError:
        return None
    if not report_descriptor:
        return None
    return {{
        "hid_parent": hid_parent,
        "hidraw_path": os.path.join("/dev", os.path.basename(hidraw_candidates[0])),
        "report_descriptor": report_descriptor[:HIDRAW_BUFFER_MAX],
        "report_lengths": parse_report_descriptor(report_descriptor),
        "country": read_int_file(os.path.join(hid_parent, "country")),
    }}


def list_controllers():
    devices = []
    virtual_event_paths = active_virtual_event_paths()
    for event_path in sorted(glob.glob("/dev/input/event*")):
        try:
            device = InputDevice(event_path)
            capabilities = device.capabilities(absinfo=False)
        except OSError:
            continue

        if (device.info.vendor, device.info.product) == SUNSHINE_INPUT_ID:
            device.close()
            continue
        if realpath_safe(event_path) in virtual_event_paths:
            device.close()
            continue
        if is_bridge_phys(device.phys):
            device.close()
            continue

        keys = set(capabilities.get(ecodes.EV_KEY, []))
        abs_axes = set(capabilities.get(ecodes.EV_ABS, []))
        if not (keys & set(GAMEPAD_BUTTON_CODES)) and not (abs_axes & set(GAMEPAD_ABS_CODES)):
            device.close()
            continue

        name = safe_string(device.name) or os.path.basename(event_path)
        devices.append(
            {{
                "event_path": event_path,
                "fingerprint": {{
                    "by_id": preferred_event_symlink(event_path),
                    "uniq": safe_string(device.uniq),
                    "phys": safe_string(device.phys),
                    "vendor_id": f"{{device.info.vendor:04x}}",
                    "product_id": f"{{device.info.product:04x}}",
                    "name": name,
                }},
            }}
        )
        device.close()
    return devices


def identity_for_status(device):
    return {{
        "source_name": safe_string(device.name),
        "source_vendor": f"{{device.info.vendor:04x}}",
        "source_product": f"{{device.info.product:04x}}",
        "source_version": f"{{device.info.version:04x}}",
    }}


def read_sys_text(path):
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def parse_uevent(path):
    data = {{}}
    for line in read_sys_text(path).splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key] = value
    return data


def realpath_safe(path):
    path = safe_string(path)
    if not path:
        return ""
    try:
        return os.path.realpath(path)
    except OSError:
        return path


def ensure_acl(path):
    path = safe_string(path)
    if not path or not os.path.exists(path):
        return
    if not shutil.which("setfacl"):
        return
    user_name = current_user_name()
    if not user_name:
        return
    result = subprocess.run(
        ["setfacl", "-m", f"u:{{user_name}}:rw", path],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = safe_string(result.stderr) or f"exit {{result.returncode}}"
        LOGGER.info("setfacl failed for %s: %s", path, stderr)


def input_identity_matches(event_path, source):
    event_name = os.path.basename(event_path)
    sys_input = Path("/sys/class/input") / event_name / "device"
    return (
        read_sys_text(sys_input / "name") == safe_string(source.name)
        and read_sys_text(sys_input / "uniq") == safe_string(source.uniq)
        and read_sys_text(sys_input / "id" / "vendor").lower().removeprefix("0x") == f"{{source.info.vendor:04x}}"
        and read_sys_text(sys_input / "id" / "product").lower().removeprefix("0x") == f"{{source.info.product:04x}}"
    )


def hidraw_identity_matches(hidraw_path, source):
    sys_hidraw = Path("/sys/class/hidraw") / os.path.basename(hidraw_path) / "device"
    uevent = parse_uevent(sys_hidraw / "uevent")
    hid_id = safe_string(uevent.get("HID_ID")).upper()
    expected_hid_id = f"{{source.info.bustype:04X}}:{{source.info.vendor:08X}}:{{source.info.product:08X}}"
    return (
        safe_string(uevent.get("HID_NAME")) == safe_string(source.name)
        and safe_string(uevent.get("HID_UNIQ")) == safe_string(source.uniq)
        and hid_id == expected_hid_id
    )


def snapshot_device_nodes():
    return {{
        "event_paths": {{realpath_safe(path): path for path in sorted(glob.glob("/dev/input/event*"))}},
        "hidraw_paths": {{realpath_safe(path): path for path in sorted(glob.glob("/dev/hidraw*"))}},
    }}


def active_virtual_event_paths():
    paths = set()
    for item in RUNTIME_STATUS.values():
        path = safe_string(item.get("virtual_event_path"))
        if path:
            paths.add(realpath_safe(path))
    return paths


def detect_virtual_nodes(before_nodes, source, source_hidraw_path=""):
    nodes = {{
        "virtual_event_path": "",
        "virtual_hidraw_path": "",
    }}
    source_event_realpath = realpath_safe(getattr(source, "path", ""))
    source_hidraw_realpath = realpath_safe(source_hidraw_path)
    for _ in range(20):
        after_nodes = snapshot_device_nodes()
        if not nodes["virtual_event_path"]:
            for real_path, event_path in after_nodes["event_paths"].items():
                if real_path in before_nodes["event_paths"]:
                    continue
                if real_path == source_event_realpath:
                    continue
                if real_path in active_virtual_event_paths():
                    continue
                if input_identity_matches(event_path, source):
                    nodes["virtual_event_path"] = event_path
                    break
        if not nodes["virtual_hidraw_path"]:
            for real_path, hidraw_path in after_nodes["hidraw_paths"].items():
                if real_path in before_nodes["hidraw_paths"]:
                    continue
                if real_path == source_hidraw_realpath:
                    continue
                if hidraw_identity_matches(hidraw_path, source):
                    nodes["virtual_hidraw_path"] = hidraw_path
                    break
        if nodes["virtual_event_path"] or nodes["virtual_hidraw_path"]:
            break
        time.sleep(0.1)
    return nodes


def refresh_virtual_nodes(selection, status_details, wait_seconds=0.0):
    current = RUNTIME_STATUS.get(selection["selection_id"], {{}})
    if not status_details.get("virtual_event_path"):
        status_details["virtual_event_path"] = safe_string(current.get("virtual_event_path"))
    if not status_details.get("virtual_hidraw_path"):
        status_details["virtual_hidraw_path"] = safe_string(current.get("virtual_hidraw_path"))
    if wait_seconds > 0:
        time.sleep(wait_seconds)


def write_status():
    payload = {{
        "updated_at": int(time.time()),
        "devices": sorted(RUNTIME_STATUS.values(), key=lambda item: item.get("label", "")),
    }}
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = STATUS_PATH.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(STATUS_PATH)


def set_status(selection, state, message="", matched_path="", virtual_name="", **details):
    with STATUS_LOCK:
        RUNTIME_STATUS[selection["selection_id"]] = {{
            "selection_id": selection["selection_id"],
            "label": selection["label"],
            "state": state,
            "message": message,
            "matched_path": matched_path,
            "virtual_name": virtual_name,
        }}
        RUNTIME_STATUS[selection["selection_id"]].update(details)
        write_status()


def update_bridge_status(selection, status_details, matched_path, virtual_name, message="Controller grabbed and bridged"):
    refresh_virtual_nodes(selection, status_details)
    ensure_acl(status_details.get("virtual_event_path", ""))
    ensure_acl(status_details.get("virtual_hidraw_path", ""))
    set_status(
        selection,
        "bridged",
        message,
        matched_path,
        virtual_name,
        **status_details,
    )


def clear_status():
    with STATUS_LOCK:
        RUNTIME_STATUS.clear()
        try:
            STATUS_PATH.unlink()
        except OSError:
            pass


def claim_path(event_path):
    with CLAIMS_LOCK:
        if event_path in CLAIMED_PATHS:
            return False
        CLAIMED_PATHS.add(event_path)
        return True


def release_path(event_path):
    with CLAIMS_LOCK:
        CLAIMED_PATHS.discard(event_path)


def find_match(selection):
    for device_info in list_controllers():
        if not selection_matches(selection, device_info):
            continue
        event_path = device_info["event_path"]
        if claim_path(event_path):
            return event_path
    return ""


def erase_all_effects(source, effect_map):
    for physical_id in list(effect_map.values()):
        try:
            source.erase_effect(physical_id)
        except OSError:
            pass
    effect_map.clear()


def _report_ioctl_request(prefix, report_len):
    size = max(1, min(report_len, HIDRAW_BUFFER_MAX))
    if prefix == "get_feature":
        return _IOWR(ord("H"), 0x07, size)
    if prefix == "set_feature":
        return _IOWR(ord("H"), 0x06, size)
    if prefix == "get_output":
        return _IOWR(ord("H"), 0x0C, size)
    if prefix == "set_output":
        return _IOWR(ord("H"), 0x0B, size)
    if prefix == "get_input":
        return _IOWR(ord("H"), 0x0A, size)
    if prefix == "set_input":
        return _IOWR(ord("H"), 0x09, size)
    raise ValueError(prefix)


def hid_set_report(hidraw_fd, report_type, payload):
    payload = bytes(payload[:HIDRAW_BUFFER_MAX])
    if not payload:
        payload = b"\\x00"
    if report_type == UHID_FEATURE_REPORT:
        request = _report_ioctl_request("set_feature", len(payload))
    elif report_type == UHID_OUTPUT_REPORT:
        request = _report_ioctl_request("set_output", len(payload))
    else:
        request = _report_ioctl_request("set_input", len(payload))
    buffer = bytearray(payload)
    fcntl.ioctl(hidraw_fd, request, buffer, True)


def hid_get_report(hidraw_fd, report_lengths, report_type, report_num):
    kind = hidraw_report_type_from_uhid(report_type)
    kind_lengths = report_lengths.get(kind, {{}})
    report_len = kind_lengths.get(report_num) or max(kind_lengths.values(), default=64)
    report_len = max(1, min(report_len, HIDRAW_BUFFER_MAX))
    if report_type == UHID_FEATURE_REPORT:
        request = _report_ioctl_request("get_feature", report_len)
    elif report_type == UHID_OUTPUT_REPORT:
        request = _report_ioctl_request("get_output", report_len)
    else:
        request = _report_ioctl_request("get_input", report_len)
    buffer = bytearray(report_len)
    buffer[0] = report_num & 0xFF
    fcntl.ioctl(hidraw_fd, request, buffer, True)
    return bytes(buffer)


def build_uhid_create_event(selection, source, hid_details):
    event = UHIDEvent()
    event.type = UHID_CREATE2
    _copy_bytes(event.u.create2.name, safe_string(source.name) or selection["label"])
    _copy_bytes(event.u.create2.phys, safe_string(source.phys))
    _copy_bytes(event.u.create2.uniq, safe_string(source.uniq))
    event.u.create2.rd_size = min(len(hid_details["report_descriptor"]), HIDRAW_BUFFER_MAX)
    event.u.create2.bus = source.info.bustype
    event.u.create2.vendor = source.info.vendor
    event.u.create2.product = source.info.product
    event.u.create2.version = source.info.version
    event.u.create2.country = hid_details.get("country", 0)
    for index, value in enumerate(hid_details["report_descriptor"][: event.u.create2.rd_size]):
        event.u.create2.rd_data[index] = value
    return event


def build_uhid_input_event(payload):
    event = UHIDEvent()
    event.type = UHID_INPUT2
    payload = bytes(payload[:UHID_DATA_MAX])
    event.u.input2.size = len(payload)
    for index, value in enumerate(payload):
        event.u.input2.data[index] = value
    return event


def build_get_report_reply(request_id, err_value, payload=b""):
    event = UHIDEvent()
    event.type = UHID_GET_REPORT_REPLY
    event.u.get_report_reply.id = request_id
    event.u.get_report_reply.err = err_value
    payload = bytes(payload[:UHID_DATA_MAX])
    event.u.get_report_reply.size = len(payload)
    for index, value in enumerate(payload):
        event.u.get_report_reply.data[index] = value
    return event


def build_set_report_reply(request_id, err_value):
    event = UHIDEvent()
    event.type = UHID_SET_REPORT_REPLY
    event.u.set_report_reply.id = request_id
    event.u.set_report_reply.err = err_value
    return event


def read_uhid_event(uhid_fd):
    size = ctypes.sizeof(UHIDEvent)
    try:
        payload = os.read(uhid_fd, size)
    except BlockingIOError:
        return None
    if not payload:
        raise OSError(errno.ENODEV, "UHID device closed")
    if len(payload) < size:
        payload = payload + (b"\\x00" * (size - len(payload)))
    return UHIDEvent.from_buffer_copy(payload[:size])


def write_uhid_event(uhid_fd, event):
    os.write(uhid_fd, bytes(event))


def destroy_uhid_device(uhid_fd):
    try:
        event = UHIDEvent()
        event.type = UHID_DESTROY
        write_uhid_event(uhid_fd, event)
    except OSError:
        pass


def handle_ff_event(selection, source, virtual, effect_map, event, status_details, matched_path):
    if event.type == ecodes.EV_UINPUT and event.code == ecodes.UI_FF_UPLOAD:
        upload = virtual.begin_upload(event.value)
        try:
            virtual_id = int(upload.effect.id)
            previous_id = effect_map.get(virtual_id)
            if previous_id is not None:
                try:
                    source.erase_effect(previous_id)
                except OSError:
                    pass
            physical_id = source.upload_effect(upload.effect)
            effect_map[virtual_id] = physical_id
            upload.retval = 0
            status_details["ff_upload_count"] = int(status_details.get("ff_upload_count", 0)) + 1
            update_bridge_status(selection, status_details, matched_path, safe_string(virtual.name))
        except OSError as error:
            upload.retval = -abs(getattr(error, "errno", 1) or 1)
            LOGGER.info("ff upload failed for %s: %s", selection["label"], error)
            set_status(
                selection,
                "bridged",
                f"Controller grabbed; rumble upload failed: {{error}}",
                matched_path,
                safe_string(virtual.name),
                **status_details,
            )
        finally:
            virtual.end_upload(upload)
        return

    if event.type == ecodes.EV_UINPUT and event.code == ecodes.UI_FF_ERASE:
        erase = virtual.begin_erase(event.value)
        try:
            virtual_id = int(erase.effect_id)
            physical_id = effect_map.pop(virtual_id, None)
            if physical_id is not None:
                source.erase_effect(physical_id)
            erase.retval = 0
            status_details["ff_erase_count"] = int(status_details.get("ff_erase_count", 0)) + 1
            update_bridge_status(selection, status_details, matched_path, safe_string(virtual.name))
        except OSError as error:
            erase.retval = -abs(getattr(error, "errno", 1) or 1)
            LOGGER.info("ff erase failed for %s: %s", selection["label"], error)
        finally:
            virtual.end_erase(erase)
        return

    if event.type == ecodes.EV_FF:
        physical_id = effect_map.get(event.code)
        if physical_id is None:
            return
        try:
            source.write(ecodes.EV_FF, physical_id, event.value)
            source.syn()
            status_details["ff_play_count"] = int(status_details.get("ff_play_count", 0)) + 1
            update_bridge_status(selection, status_details, matched_path, safe_string(virtual.name))
        except OSError as error:
            LOGGER.info("ff playback failed for %s: %s", selection["label"], error)
            set_status(
                selection,
                "bridged",
                f"Controller grabbed; rumble playback failed: {{error}}",
                matched_path,
                safe_string(virtual.name),
                **status_details,
            )


def bridge_evdev_controller(selection, source, event_path, hid_details=None, fallback_reason=""):
    virtual = None
    effect_map = {{}}
    try:
        before_nodes = snapshot_device_nodes()
        source_caps = source.capabilities()
        supports_ff = ecodes.EV_FF in source_caps
        filtered_types = (ecodes.EV_SYN,) if supports_ff else (ecodes.EV_SYN, ecodes.EV_FF)
        virtual = UInput.from_device(
            source,
            filtered_types=filtered_types,
            name=safe_string(source.name) or selection["label"],
            vendor=source.info.vendor,
            product=source.info.product,
            version=source.info.version,
            bustype=source.info.bustype,
            phys=safe_string(source.phys) or None,
            input_props=source.input_props(),
        )
        status_details = identity_for_status(source)
        status_details.update(
            {{
                "bridge_mode": "uinput-fallback",
                "ff_supported": supports_ff,
                "ff_enabled": supports_ff,
                "hidraw_path": hid_details.get("hidraw_path", "") if hid_details else "",
                "hid_output_forwarding": False,
                "virtual_event_path": "",
                "virtual_hidraw_path": "",
                "virtual_vendor": f"{{virtual.vendor:04x}}",
                "virtual_product": f"{{virtual.product:04x}}",
                "virtual_version": f"{{virtual.version:04x}}",
                "ff_upload_count": 0,
                "ff_play_count": 0,
                "ff_erase_count": 0,
            }}
        )
        nodes = detect_virtual_nodes(before_nodes, source)
        status_details["virtual_event_path"] = nodes.get("virtual_event_path", "")
        status_details["virtual_hidraw_path"] = nodes.get("virtual_hidraw_path", "")
        message = "Controller grabbed and bridged"
        if fallback_reason:
            message += f" (uinput fallback: {{fallback_reason}})"
        virtual_devnode = safe_string(getattr(virtual, "devnode", ""))
        if virtual_devnode and not status_details["virtual_event_path"]:
            status_details["virtual_event_path"] = virtual_devnode
        update_bridge_status(selection, status_details, event_path, safe_string(virtual.name), message)
        LOGGER.info(
            "bridging %s from %s via uinput fallback as %s %04x:%04x ff=%s",
            selection["label"],
            event_path,
            safe_string(virtual.name),
            virtual.vendor,
            virtual.product,
            supports_ff,
        )

        while not STOP_EVENT.is_set():
            ready, _, _ = select.select([source, virtual], [], [], 1.0)
            if not ready:
                continue
            if source in ready:
                for input_event in source.read():
                    if input_event.type == ecodes.EV_SYN:
                        virtual.syn()
                        continue
                    virtual.write_event(input_event)
            if virtual in ready and supports_ff:
                for input_event in virtual.read():
                    handle_ff_event(selection, source, virtual, effect_map, input_event, status_details, event_path)
    finally:
        erase_all_effects(source, effect_map)
        if virtual is not None:
            virtual.close()


def bridge_hid_controller(selection, source, event_path, hid_details):
    source_hid_fd = None
    uhid_fd = None
    try:
        before_nodes = snapshot_device_nodes()
        source_hid_fd = os.open(hid_details["hidraw_path"], os.O_RDWR | os.O_NONBLOCK)
        uhid_fd = os.open("/dev/uhid", os.O_RDWR | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
        create_event = build_uhid_create_event(selection, source, hid_details)
        write_uhid_event(uhid_fd, create_event)

        status_details = identity_for_status(source)
        status_details.update(
            {{
                "bridge_mode": "uhid",
                "ff_supported": True,
                "ff_enabled": True,
                "hidraw_path": hid_details["hidraw_path"],
                "hid_output_forwarding": True,
                "virtual_event_path": "",
                "virtual_hidraw_path": "",
                "virtual_vendor": f"{{source.info.vendor:04x}}",
                "virtual_product": f"{{source.info.product:04x}}",
                "virtual_version": f"{{source.info.version:04x}}",
                "uhid_output_count": 0,
                "uhid_get_report_count": 0,
                "uhid_set_report_count": 0,
                "uhid_open_count": 0,
                "uhid_close_count": 0,
                "last_uhid_event": "",
                "last_uhid_event_at": 0,
            }}
        )
        nodes = detect_virtual_nodes(before_nodes, source, hid_details["hidraw_path"])
        status_details["virtual_event_path"] = nodes.get("virtual_event_path", "")
        status_details["virtual_hidraw_path"] = nodes.get("virtual_hidraw_path", "")
        update_bridge_status(
            selection,
            status_details,
            event_path,
            safe_string(source.name) or selection["label"],
        )
        LOGGER.info(
            "bridging %s from %s via uhid hidraw=%s",
            selection["label"],
            event_path,
            hid_details["hidraw_path"],
        )

        while not STOP_EVENT.is_set():
            ready, _, _ = select.select([source_hid_fd, uhid_fd], [], [], 1.0)
            if not ready:
                continue

            if source_hid_fd in ready:
                payload = os.read(source_hid_fd, UHID_DATA_MAX)
                if not payload:
                    raise OSError(errno.ENODEV, "HID source disconnected")
                write_uhid_event(uhid_fd, build_uhid_input_event(payload))

            if uhid_fd in ready:
                while True:
                    uhid_event = read_uhid_event(uhid_fd)
                    if uhid_event is None:
                        break
                    if uhid_event.type == UHID_OUTPUT:
                        payload = bytes(uhid_event.u.output.data[: uhid_event.u.output.size])
                        status_details["uhid_output_count"] = int(status_details.get("uhid_output_count", 0)) + 1
                        status_details["last_uhid_event"] = f"output rtype={{uhid_event.u.output.rtype}} size={{uhid_event.u.output.size}}"
                        status_details["last_uhid_event_at"] = int(time.time())
                        LOGGER.info(
                            "uhid output for %s: rtype=%s size=%s",
                            selection["label"],
                            uhid_event.u.output.rtype,
                            uhid_event.u.output.size,
                        )
                        hid_set_report(source_hid_fd, uhid_event.u.output.rtype, payload)
                        update_bridge_status(selection, status_details, event_path, safe_string(source.name) or selection["label"])
                        continue
                    if uhid_event.type == UHID_GET_REPORT:
                        status_details["uhid_get_report_count"] = int(status_details.get("uhid_get_report_count", 0)) + 1
                        status_details["last_uhid_event"] = f"get-report rtype={{uhid_event.u.get_report.rtype}} rnum={{uhid_event.u.get_report.rnum}}"
                        status_details["last_uhid_event_at"] = int(time.time())
                        LOGGER.info(
                            "uhid get-report for %s: rtype=%s rnum=%s",
                            selection["label"],
                            uhid_event.u.get_report.rtype,
                            uhid_event.u.get_report.rnum,
                        )
                        try:
                            payload = hid_get_report(
                                source_hid_fd,
                                hid_details["report_lengths"],
                                uhid_event.u.get_report.rtype,
                                uhid_event.u.get_report.rnum,
                            )
                            reply = build_get_report_reply(uhid_event.u.get_report.id, 0, payload)
                        except OSError as error:
                            reply = build_get_report_reply(
                                uhid_event.u.get_report.id,
                                abs(getattr(error, "errno", errno.EIO) or errno.EIO),
                            )
                        write_uhid_event(uhid_fd, reply)
                        update_bridge_status(selection, status_details, event_path, safe_string(source.name) or selection["label"])
                        continue
                    if uhid_event.type == UHID_SET_REPORT:
                        status_details["uhid_set_report_count"] = int(status_details.get("uhid_set_report_count", 0)) + 1
                        status_details["last_uhid_event"] = f"set-report rtype={{uhid_event.u.set_report.rtype}} size={{uhid_event.u.set_report.size}}"
                        status_details["last_uhid_event_at"] = int(time.time())
                        LOGGER.info(
                            "uhid set-report for %s: rtype=%s size=%s",
                            selection["label"],
                            uhid_event.u.set_report.rtype,
                            uhid_event.u.set_report.size,
                        )
                        try:
                            payload = bytes(uhid_event.u.set_report.data[: uhid_event.u.set_report.size])
                            hid_set_report(source_hid_fd, uhid_event.u.set_report.rtype, payload)
                            reply = build_set_report_reply(uhid_event.u.set_report.id, 0)
                        except OSError as error:
                            reply = build_set_report_reply(
                                uhid_event.u.set_report.id,
                                abs(getattr(error, "errno", errno.EIO) or errno.EIO),
                            )
                        write_uhid_event(uhid_fd, reply)
                        update_bridge_status(selection, status_details, event_path, safe_string(source.name) or selection["label"])
                        continue
                    if uhid_event.type == UHID_OPEN:
                        status_details["uhid_open_count"] = int(status_details.get("uhid_open_count", 0)) + 1
                        status_details["last_uhid_event"] = "open"
                        status_details["last_uhid_event_at"] = int(time.time())
                        update_bridge_status(selection, status_details, event_path, safe_string(source.name) or selection["label"])
                        continue
                    if uhid_event.type == UHID_CLOSE:
                        status_details["uhid_close_count"] = int(status_details.get("uhid_close_count", 0)) + 1
                        status_details["last_uhid_event"] = "close"
                        status_details["last_uhid_event_at"] = int(time.time())
                        update_bridge_status(selection, status_details, event_path, safe_string(source.name) or selection["label"])
                        continue
                    if uhid_event.type in (UHID_START, UHID_STOP, UHID_OPEN, UHID_CLOSE):
                        continue
                    break
    finally:
        if uhid_fd is not None:
            destroy_uhid_device(uhid_fd)
            os.close(uhid_fd)
        if source_hid_fd is not None:
            os.close(source_hid_fd)


def bridge_loop(selection):
    set_status(selection, "waiting", "Waiting for controller")
    while not STOP_EVENT.is_set():
        event_path = find_match(selection)
        if not event_path:
            set_status(selection, "waiting", "Controller not detected")
            STOP_EVENT.wait(2.0)
            continue

        source = None
        keep_waiting_status = True
        try:
            source = InputDevice(event_path)
            source.grab()
            hid_details = discover_hid_details(event_path)
            if hid_details:
                try:
                    bridge_hid_controller(selection, source, event_path, hid_details)
                except OSError as error:
                    LOGGER.info("uhid bridge failed for %s: %s", selection["label"], error)
                    bridge_evdev_controller(selection, source, event_path, hid_details, str(error))
            else:
                bridge_evdev_controller(selection, source, event_path)
        except OSError as error:
            keep_waiting_status = False
            set_status(selection, "error", str(error), event_path)
            LOGGER.info("bridge error for %s: %s", selection["label"], error)
            STOP_EVENT.wait(2.0)
        except Exception as error:
            keep_waiting_status = False
            set_status(selection, "error", str(error), event_path)
            LOGGER.exception("bridge error for %s", selection["label"])
            STOP_EVENT.wait(2.0)
        finally:
            release_path(event_path)
            if source is not None:
                try:
                    source.ungrab()
                except OSError:
                    pass
                source.close()
            if keep_waiting_status and not STOP_EVENT.is_set():
                set_status(selection, "waiting", "Waiting for controller")


def handle_signal(signum, _frame):
    LOGGER.info("received signal %s, stopping", signum)
    STOP_EVENT.set()


def main():
    selections = load_selections()
    if not selections:
        clear_status()
        LOGGER.info("no exclusive input devices configured")
        return 0

    for signal_name in ("SIGINT", "SIGTERM", "SIGHUP"):
        if hasattr(signal, signal_name):
            signal.signal(getattr(signal, signal_name), handle_signal)

    threads = []
    for selection in selections:
        thread = threading.Thread(target=bridge_loop, args=(selection,), daemon=True)
        thread.start()
        threads.append(thread)

    while not STOP_EVENT.is_set():
        time.sleep(1.0)

    for thread in threads:
        thread.join(timeout=5.0)
    clear_status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""


def _script_templates(state: Dict[str, Any]) -> Dict[Path, str]:
    paths = state["paths"]
    sunshine_command = _sunshine_binary() or "sunshine"
    audio_sink = state["audio_sink"]
    managed_audio_sinks = _managed_audio_sink_names(state)
    managed_audio_sources = _managed_audio_source_names(state)
    sunshine_conf_root = Path(paths["sunshine_conf"]).parent
    return {
        Path(paths["sway_config"]): f"""# Managed by LutrisToSunshine virtualdisplay.
output HEADLESS-1 resolution {FALLBACK_WIDTH}x{FALLBACK_HEIGHT}@{FALLBACK_FPS}Hz
output * allow_tearing yes
output * max_render_time off

exec swaybg -c '#111827'

input * events disabled
input "48879:57005:Keyboard_passthrough" events enabled
input "48879:57005:Mouse_passthrough" events enabled
input "48879:57005:Mouse_passthrough_(absolute)" events enabled
input "48879:57005:Touch_passthrough" events enabled
input "48879:57005:Pen_passthrough" events enabled
input "1356:3302:Sunshine_PS5_(virtual)_pad_Touchpad" events enabled

input "48879:57005:Mouse_passthrough" accel_profile flat
input "48879:57005:Mouse_passthrough_(absolute)" accel_profile flat
""",
        Path(paths["sway_start_script"]): f"""#!/bin/bash
set -euo pipefail

runtime_dir="${{XDG_RUNTIME_DIR:-/run/user/$(id -u)}}"
display_file="{paths['wayland_display_file']}"
before_file="$(mktemp)"
after_file="$(mktemp)"
path_value="${{PATH:-/usr/local/bin:/usr/bin:/bin}}"
lang_value="${{LANG:-C.UTF-8}}"
home_value="${{HOME:-/home/$(id -un)}}"
user_value="${{USER:-$(id -un)}}"
logname_value="${{LOGNAME:-$user_value}}"
shell_value="${{SHELL:-/bin/sh}}"
dbus_value="${{DBUS_SESSION_BUS_ADDRESS:-unix:path=$runtime_dir/bus}}"

cleanup() {{
    rm -f "$before_file" "$after_file"
}}
trap cleanup EXIT

rm -f "$display_file" "{state['sway_socket']}"
ls "$runtime_dir"/wayland-* 2>/dev/null | grep -v '\\.lock$' | sort > "$before_file" || true

unset DISPLAY
unset WAYLAND_DISPLAY
unset DESKTOP_SESSION
unset SESSION_MANAGER
unset XDG_SESSION_DESKTOP
unset XDG_ACTIVATION_TOKEN
unset DESKTOP_STARTUP_ID
unset KDE_FULL_SESSION
unset KDE_SESSION_UID
unset KDE_SESSION_VERSION

/usr/bin/env -i \
    HOME="$home_value" \
    USER="$user_value" \
    LOGNAME="$logname_value" \
    SHELL="$shell_value" \
    PATH="$path_value" \
    LANG="$lang_value" \
    XDG_RUNTIME_DIR="$runtime_dir" \
    DBUS_SESSION_BUS_ADDRESS="$dbus_value" \
    XDG_SESSION_TYPE=wayland \
    XDG_CURRENT_DESKTOP=sway \
    XDG_SESSION_DESKTOP=sway \
    SWAYSOCK="{state['sway_socket']}" \
    WLR_BACKENDS=headless,libinput \
    LIBSEAT_BACKEND=noop \
    /usr/bin/sway --config "{paths['sway_config']}" &
sway_pid=$!

for _ in $(seq 1 100); do
    if ! kill -0 "$sway_pid" 2>/dev/null; then
        break
    fi
    ls "$runtime_dir"/wayland-* 2>/dev/null | grep -v '\\.lock$' | sort > "$after_file" || true
    new_socket="$(comm -13 "$before_file" "$after_file" | head -n1)"
    if [ -n "$new_socket" ]; then
        basename "$new_socket" > "$display_file"
        break
    fi
    sleep 0.1
done

if [ ! -s "$display_file" ]; then
    kill "$sway_pid" 2>/dev/null || true
    wait "$sway_pid" 2>/dev/null || true
    echo "Unable to determine the headless sway Wayland socket." >&2
    exit 1
fi

wait "$sway_pid"
""",
        Path(paths["sunshine_start_script"]): f"""#!/bin/bash
set -euo pipefail

display_file="{paths['wayland_display_file']}"
if [ ! -s "$display_file" ]; then
    echo "Headless sway display is not ready." >&2
    exit 1
fi

runtime_dir="${{XDG_RUNTIME_DIR:-/run/user/$(id -u)}}"
path_value="${{PATH:-/usr/local/bin:/usr/bin:/bin}}"
lang_value="${{LANG:-C.UTF-8}}"
home_value="${{HOME:-/home/$(id -un)}}"
user_value="${{USER:-$(id -un)}}"
logname_value="${{LOGNAME:-$user_value}}"
shell_value="${{SHELL:-/bin/sh}}"
dbus_value="${{DBUS_SESSION_BUS_ADDRESS:-unix:path=$runtime_dir/bus}}"
wayland_value="$(cat "$display_file")"
cd "{sunshine_conf_root}"
exec /usr/bin/env -i \
    HOME="$home_value" \
    USER="$user_value" \
    LOGNAME="$logname_value" \
    SHELL="$shell_value" \
    PATH="$path_value" \
    LANG="$lang_value" \
    XDG_RUNTIME_DIR="$runtime_dir" \
    DBUS_SESSION_BUS_ADDRESS="$dbus_value" \
    WAYLAND_DISPLAY="$wayland_value" \
    SWAYSOCK="{state['sway_socket']}" \
    XDG_SESSION_TYPE=wayland \
    XDG_CURRENT_DESKTOP=sway \
    XDG_SESSION_DESKTOP=sway \
    {sunshine_command}
""",
        Path(paths["sunshine_wrapper_script"]): f"""#!/bin/bash
set -euo pipefail

state_path="{paths['state_path']}"
sunshine_conf="{paths['sunshine_conf']}"
audio_sink="{audio_sink}"
audio_create_script="{paths['audio_create_script']}"
audio_cleanup_script="{paths['audio_cleanup_script']}"
audio_guard_script="{paths['audio_guard_script']}"
input_bridge_script="{paths['input_bridge_script']}"
sway_start_script="{paths['sway_start_script']}"
sunshine_start_script="{paths['sunshine_start_script']}"
display_file="{paths['wayland_display_file']}"
bridge_status_file="{paths['input_bridge_status_file']}"
sway_socket="{state['sway_socket']}"
audio_guard_pid=""
input_bridge_pid=""
sway_pid=""
sunshine_status=0

prepare_audio_state() {{
    python3 - "$state_path" "$sunshine_conf" "$audio_sink" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

state_path = Path(sys.argv[1])
conf_path = Path(sys.argv[2])
managed_sink = sys.argv[3]
managed_sinks = set({managed_audio_sinks!r})
managed_sources = set({managed_audio_sources!r})

try:
    state = json.loads(state_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    state = {{}}
if not isinstance(state, dict):
    state = {{}}

host_defaults = state.get("host_audio_defaults") or {{}}
if not isinstance(host_defaults, dict):
    host_defaults = {{}}

lines = conf_path.read_text(encoding="utf-8").splitlines() if conf_path.exists() else []
current_value = ""
present = False
for line in lines:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        continue
    key, value = stripped.split("=", 1)
    if key.strip() == "audio_sink":
        present = True
        current_value = value.strip()
        break

original = state.get("sunshine_audio_sink")
if not isinstance(original, dict) or "present" not in original:
    state["sunshine_audio_sink"] = {{"present": present, "value": current_value}}

updated_lines = []
replaced = False
for line in lines:
    stripped = line.strip()
    if stripped and not stripped.startswith("#") and "=" in stripped:
        key = stripped.split("=", 1)[0].strip()
        if key == "audio_sink":
            updated_lines.append(f"audio_sink = {{managed_sink}}")
            replaced = True
            continue
    updated_lines.append(line)
if not replaced:
    if updated_lines and updated_lines[-1] != "":
        updated_lines.append("")
    updated_lines.append(f"audio_sink = {{managed_sink}}")
conf_path.parent.mkdir(parents=True, exist_ok=True)
conf_path.write_text("\\n".join(updated_lines) + "\\n", encoding="utf-8")

try:
    pactl = subprocess.run(
        ["pactl", "info"],
        text=True,
        capture_output=True,
        check=False,
    )
except OSError:
    pactl = None
if pactl and pactl.returncode == 0:
    current_sink = ""
    current_source = ""
    for line in pactl.stdout.splitlines():
        if line.startswith("Default Sink: "):
            current_sink = line.split(": ", 1)[1].strip()
        elif line.startswith("Default Source: "):
            current_source = line.split(": ", 1)[1].strip()
    if current_sink and current_source:
        if not (
            current_sink in managed_sinks
            and current_source in managed_sources
            and host_defaults.get("sink")
            and host_defaults.get("source")
        ):
            state["host_audio_defaults"] = {{"sink": current_sink, "source": current_source}}

state_path.parent.mkdir(parents=True, exist_ok=True)
state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
PY
}}

restore_audio_state() {{
    python3 - "$state_path" "$sunshine_conf" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

state_path = Path(sys.argv[1])
conf_path = Path(sys.argv[2])
try:
    state = json.loads(state_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(0)
if not isinstance(state, dict):
    raise SystemExit(0)

original = state.get("sunshine_audio_sink") or {{}}
host_defaults = state.get("host_audio_defaults") or {{}}
if not isinstance(original, dict):
    original = {{}}
if not isinstance(host_defaults, dict):
    host_defaults = {{}}

lines = conf_path.read_text(encoding="utf-8").splitlines() if conf_path.exists() else []
updated_lines = []
found = False
for line in lines:
    stripped = line.strip()
    if stripped and not stripped.startswith("#") and "=" in stripped:
        key = stripped.split("=", 1)[0].strip()
        if key == "audio_sink":
            found = True
            if original.get("present"):
                updated_lines.append(f"audio_sink = {{str(original.get('value') or '').strip()}}")
            continue
    updated_lines.append(line)
if not found and original.get("present"):
    if updated_lines and updated_lines[-1] != "":
        updated_lines.append("")
    updated_lines.append(f"audio_sink = {{str(original.get('value') or '').strip()}}")
conf_path.parent.mkdir(parents=True, exist_ok=True)
conf_path.write_text("\\n".join(updated_lines) + "\\n", encoding="utf-8")

sink_name = str(host_defaults.get("sink") or "").strip()
source_name = str(host_defaults.get("source") or "").strip()
if sink_name:
    subprocess.run(["pactl", "set-default-sink", sink_name], text=True, capture_output=True, check=False)
if source_name:
    subprocess.run(["pactl", "set-default-source", source_name], text=True, capture_output=True, check=False)
state["host_audio_defaults"] = {{"sink": "", "source": ""}}
state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
PY
}}

input_bridge_enabled() {{
    python3 - "$state_path" <<'PY'
import json
import sys
from pathlib import Path

state_path = Path(sys.argv[1])
try:
    state = json.loads(state_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)
devices = ((state.get("exclusive_input_devices") or {{}}).get("devices") or [])
raise SystemExit(0 if devices else 1)
PY
}}

stop_child() {{
    local pid="${{1:-}}"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        kill -- "-$pid" >/dev/null 2>&1 || kill "$pid" >/dev/null 2>&1 || true
        wait "$pid" >/dev/null 2>&1 || true
    fi
}}

cleanup() {{
    local exit_code=$?
    stop_child "$sunshine_pid"
    stop_child "$input_bridge_pid"
    stop_child "$audio_guard_pid"
    stop_child "$sway_pid"
    rm -f "$bridge_status_file" "$display_file"
    "$audio_cleanup_script" >/dev/null 2>&1 || true
    restore_audio_state >/dev/null 2>&1 || true
    exit "$exit_code"
}}

trap cleanup EXIT INT TERM HUP

prepare_audio_state
"$audio_create_script"
setsid "$sway_start_script" &
sway_pid=$!

for _ in $(seq 1 100); do
    if [ -s "$display_file" ] && [ -S "$sway_socket" ]; then
        break
    fi
    if ! kill -0 "$sway_pid" 2>/dev/null; then
        break
    fi
    sleep 0.1
done

if [ ! -s "$display_file" ] || [ ! -S "$sway_socket" ]; then
    echo "Headless sway did not become ready." >&2
    exit 1
fi

setsid "$audio_guard_script" &
audio_guard_pid=$!

if input_bridge_enabled; then
    setsid python3 "$input_bridge_script" &
    input_bridge_pid=$!
fi

setsid "$sunshine_start_script" &
sunshine_pid=$!
wait "$sunshine_pid"
sunshine_status=$?
exit "$sunshine_status"
""",
        Path(paths["audio_create_script"]): f"""#!/bin/bash
set -euo pipefail

sink_name="{audio_sink}"
module_file="{paths['audio_module_file']}"

if pactl list short sinks | awk '{{print $2}}' | grep -Fx "$sink_name" >/dev/null 2>&1; then
    rm -f "$module_file"
    exit 0
fi

module_id="$(pactl load-module module-null-sink sink_name="$sink_name" sink_properties=device.description='LutrisToSunshine Virtual Display')"
printf '%s\\n' "$module_id" > "$module_file"
""",
        Path(paths["audio_cleanup_script"]): f"""#!/bin/bash
set -euo pipefail

module_file="{paths['audio_module_file']}"
if [ ! -f "$module_file" ]; then
    exit 0
fi

module_id="$(cat "$module_file")"
if [ -n "$module_id" ]; then
    pactl unload-module "$module_id" >/dev/null 2>&1 || true
fi
rm -f "$module_file"
""",
        Path(paths["audio_guard_script"]): f"""#!/bin/bash
set -euo pipefail

state_path="{paths['state_path']}"
poll_interval="{AUDIO_GUARD_POLL_INTERVAL_SECONDS}"

is_managed_sink() {{
    local value="${{1:-}}"
    case "$value" in
{chr(10).join(f'        {shlex.quote(name)}) return 0 ;;' for name in managed_audio_sinks)}
        *) return 1 ;;
    esac
}}

is_managed_source() {{
    local value="${{1:-}}"
    case "$value" in
{chr(10).join(f'        {shlex.quote(name)}) return 0 ;;' for name in managed_audio_sources)}
        *) return 1 ;;
    esac
}}

read_host_defaults() {{
    python3 - "$state_path" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
defaults = {{}}
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    payload = {{}}
if isinstance(payload, dict):
    defaults = payload.get("host_audio_defaults") or {{}}
print(str(defaults.get("sink") or ""))
print(str(defaults.get("source") or ""))
PY
}}

while true; do
    if ! pactl_info="$(pactl info 2>/dev/null)"; then
        sleep "$poll_interval"
        continue
    fi

    current_sink="$(printf '%s\\n' "$pactl_info" | awk -F': ' '/^Default Sink:/ {{print $2; exit}}')"
    current_source="$(printf '%s\\n' "$pactl_info" | awk -F': ' '/^Default Source:/ {{print $2; exit}}')"

    mapfile -t host_defaults < <(read_host_defaults)
    host_sink="${{host_defaults[0]:-}}"
    host_source="${{host_defaults[1]:-}}"

    if [ -n "$host_sink" ] && [ "$current_sink" != "$host_sink" ] && is_managed_sink "$current_sink"; then
        pactl set-default-sink "$host_sink" >/dev/null 2>&1 || true
    fi
    if [ -n "$host_source" ] && [ "$current_source" != "$host_source" ] && is_managed_source "$current_source"; then
        pactl set-default-source "$host_source" >/dev/null 2>&1 || true
    fi

    sleep "$poll_interval"
done
""",
        Path(paths["input_bridge_script"]): _input_bridge_script(state),
        Path(paths["launch_app_script"]): f"""#!/bin/bash
set -euo pipefail

origin="${{1:-cmd}}"
exit_timeout_value="5"
encoded_command=""

if [ "${{#}}" -ge 3 ] && [[ "${{2:-}}" =~ ^[0-9]+$ ]]; then
    exit_timeout_value="${{2}}"
    encoded_command="${{3:-}}"
elif [ "${{#}}" -ge 2 ]; then
    encoded_command="${{2:-}}"
else
    encoded_command="${{1:-}}"
    origin="cmd"
fi

if [ -z "$encoded_command" ]; then
    echo "Missing encoded app command." >&2
    exit 1
fi

if [ ! -S "{state['sway_socket']}" ]; then
    echo "Headless sway IPC socket is not ready." >&2
    exit 1
fi

if [ ! -s "{paths['wayland_display_file']}" ]; then
    echo "Headless sway display is not ready." >&2
    exit 1
fi

unset DISPLAY
unset DESKTOP_SESSION
unset SESSION_MANAGER
unset XDG_SESSION_DESKTOP
unset XDG_ACTIVATION_TOKEN
unset DESKTOP_STARTUP_ID
unset KDE_FULL_SESSION
unset KDE_SESSION_UID
unset KDE_SESSION_VERSION

decoded_command="$(printf '%s' "$encoded_command" | base64 --decode)"
display_value=":1"
wayland_value="$(cat "{paths['wayland_display_file']}")"
runtime_dir="${{XDG_RUNTIME_DIR:-/run/user/$(id -u)}}"
path_value="${{PATH:-/usr/local/bin:/usr/bin:/bin}}"
lang_value="${{LANG:-C.UTF-8}}"
home_value="${{HOME:-/home/$(id -un)}}"
user_value="${{USER:-$(id -un)}}"
logname_value="${{LOGNAME:-$user_value}}"
shell_value="${{SHELL:-/bin/sh}}"
dbus_value="${{DBUS_SESSION_BUS_ADDRESS:-unix:path=$runtime_dir/bus}}"
pulse_server_value="${{PULSE_SERVER:-}}"
pulse_clientconfig_value="${{PULSE_CLIENTCONFIG:-}}"
portal_lock_file="{paths['portal_lock_file']}"
portal_active_file="{paths['portal_active_file']}"
launch_log_file="{paths['last_launch_log_file']}"
portal_timeout="{FLATPAK_PORTAL_SWITCH_TIMEOUT}"
spawn_timeout="{FLATPAK_PORTAL_SPAWN_TIMEOUT}"
restore_grace="{FLATPAK_PORTAL_RESTORE_GRACE}"
launch_id="$(python3 - <<'PY'
import uuid
print(uuid.uuid4().hex)
PY
)"
adoption_poll_interval="0.05"
adoption_exit_grace_checks="6"
post_launch_grace_checks="20"
tracked_pids_value=""
tracked_groups_value=""
last_snapshot_value=""
last_tracked_pids_value=""

mkdir -p "$(dirname "$launch_log_file")"
: > "$launch_log_file"
chmod 600 "$launch_log_file" >/dev/null 2>&1 || true

log_debug() {{
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$launch_log_file"
}}

log_pid_delta() {{
    python3 - "$last_tracked_pids_value" "$tracked_pids_value" <<'PY' >> "$launch_log_file"
import sys

previous = sorted({{int(value) for value in sys.argv[1].split() if value}})
current = sorted({{int(value) for value in sys.argv[2].split() if value}})
added = [pid for pid in current if pid not in previous]
removed = [pid for pid in previous if pid not in current]
print(f"tracked-pids added={{added}} removed={{removed}} current={{current}}")
PY
}}

log_snapshot_details() {{
    local snapshot_json="$1"
    python3 - "$snapshot_json" <<'PY' >> "$launch_log_file"
import json
import os
from pathlib import Path
import sys

snapshot = json.loads(sys.argv[1])
pids = sorted(set(snapshot.get("launch_pids", [])) | set(snapshot.get("window_pids", [])))
print("tracked-snapshot-begin")
for pid in pids:
    proc = Path("/proc") / str(pid)
    if not proc.exists():
        continue
    try:
        cmdline = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
    except OSError:
        cmdline = ""
    try:
        comm = (proc / "comm").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        comm = "?"
    fields = {{}}
    try:
        for line in (proc / "status").read_text(encoding="utf-8", errors="replace").splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                fields[key] = value.strip()
    except OSError:
        pass
    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = "?"
    marker = ""
    if "Spider-Man2.exe" in cmdline or comm == "Spider-Man2.ex":
        marker = " target=Spider-Man2.exe"
    print(
        f"  pid={{pid}} ppid={{fields.get('PPid', '?')}} pgid={{pgid}} "
        f"state={{fields.get('State', '?')}} comm={{comm}}{{marker}}"
    )
    if cmdline:
        print(f"    cmd={{cmdline}}")
print("tracked-snapshot-end")
PY
}}

is_flatpak_command() {{
    python3 - "$1" <<'PY'
import shlex
import sys

FLATPAK_SPAWN_HOST_PREFIX = ["flatpak-spawn", "--host"]

command = sys.argv[1]
try:
    tokens = shlex.split(command)
except ValueError:
    raise SystemExit(1)

index = 0
if tokens[: len(FLATPAK_SPAWN_HOST_PREFIX)] == FLATPAK_SPAWN_HOST_PREFIX:
    index = len(FLATPAK_SPAWN_HOST_PREFIX)

raise SystemExit(0 if tokens[index : index + 2] == ["flatpak", "run"] else 1)
PY
}}

launch_headless_direct() {{
    local command_to_run="$1"
    log_debug "launch command: $command_to_run"
    setsid /usr/bin/env -i \
        HOME="$home_value" \
        USER="$user_value" \
        LOGNAME="$logname_value" \
        SHELL="$shell_value" \
        PATH="$path_value" \
        LANG="$lang_value" \
        XDG_RUNTIME_DIR="$runtime_dir" \
        DBUS_SESSION_BUS_ADDRESS="$dbus_value" \
        DISPLAY="$display_value" \
        WAYLAND_DISPLAY="$wayland_value" \
        SWAYSOCK="{state['sway_socket']}" \
        XDG_SESSION_TYPE=wayland \
        XDG_CURRENT_DESKTOP=sway \
        XDG_SESSION_DESKTOP=sway \
        DESKTOP_SESSION=sway \
        LTS_LAUNCH_ID="$launch_id" \
        LTS_VDISPLAY=1 \
        PULSE_SINK="{audio_sink}" \
        PULSE_SERVER="$pulse_server_value" \
        PULSE_CLIENTCONFIG="$pulse_clientconfig_value" \
        /bin/sh -lc "$command_to_run" >>"$launch_log_file" 2>&1 &
    launch_pid="$!"
}}

collect_tracking_snapshot() {{
    python3 - "$launch_id" "$$" "{state['sway_socket']}" <<'PY'
import json
import os
import subprocess
import sys

launch_id = sys.argv[1].encode()
wrapper_pid = int(sys.argv[2])
sway_socket = sys.argv[3]


def env_matches(pid: int) -> bool:
    try:
        with open(f"/proc/{{pid}}/environ", "rb") as handle:
            environ = handle.read().split(b"\\0")
    except OSError:
        return False
    needle = b"LTS_LAUNCH_ID=" + launch_id
    return needle in environ


def walk_tree(node, on_headless=False, result=None):
    if result is None:
        result = set()
    if not isinstance(node, dict):
        return result

    output = node.get("output")
    current_headless = on_headless or output == "HEADLESS-1"
    pid = node.get("pid")
    if current_headless and isinstance(pid, int) and pid > 0:
        result.add(pid)

    for key in ("nodes", "floating_nodes"):
        for child in node.get(key) or []:
            walk_tree(child, current_headless, result)
    return result


launch_pids = set()
for entry in os.listdir("/proc"):
    if not entry.isdigit():
        continue
    pid = int(entry)
    if pid == wrapper_pid or pid == os.getpid():
        continue
    if env_matches(pid):
        launch_pids.add(pid)

window_pids = set()
try:
    result = subprocess.run(
        ["swaymsg", "-s", sway_socket, "-t", "get_tree", "-r"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout:
        tree = json.loads(result.stdout)
        window_pids = walk_tree(tree)
except Exception:
    window_pids = set()

pgids = set()
for pid in launch_pids | window_pids:
    try:
        pgid = os.getpgid(pid)
    except OSError:
        continue
    if pgid > 0:
        pgids.add(pgid)

snapshot = {{
    "launch_pids": sorted(launch_pids),
    "window_pids": sorted(window_pids),
    "pgids": sorted(pgids),
}}
print(json.dumps(snapshot))
PY
}}

update_tracking_state() {{
    local snapshot_json="$1"
    tracked_pids_value="$(python3 - "$snapshot_json" <<'PY'
import json
import sys

snapshot = json.loads(sys.argv[1])
pids = sorted(set(snapshot.get("launch_pids", [])) | set(snapshot.get("window_pids", [])))
print(" ".join(str(pid) for pid in pids))
PY
)"
    tracked_groups_value="$(python3 - "$snapshot_json" <<'PY'
import json
import sys

snapshot = json.loads(sys.argv[1])
print(" ".join(str(pgid) for pgid in snapshot.get("pgids", [])))
PY
)"
}}

write_active_state() {{
    local phase="${{1:-running}}"
    local tracked_count="0"
    if [ -n "$tracked_pids_value" ]; then
        tracked_count="$(wc -w <<<"$tracked_pids_value" | awk '{{print $1}}')"
    fi
    cat > "$portal_active_file" <<EOF
pid=$$
child_pid=$launch_pid
launch_id=$launch_id
phase=$phase
tracked_count=$tracked_count
command=$decoded_command
launch_log=$launch_log_file
EOF
}}

child_is_running() {{
    [ -n "${{launch_pid:-}}" ] && kill -0 "$launch_pid" >/dev/null 2>&1
}}

stop_child() {{
    local signal_name="${{1:-TERM}}"
    local checks

    if ! child_is_running && [ -z "$tracked_pids_value" ] && [ -z "$tracked_groups_value" ]; then
        log_debug "stop_child skipped: nothing to stop"
        return 0
    fi

    local launch_pgid
    launch_pgid=""
    if [ -n "${{launch_pid:-}}" ]; then
        launch_pgid="$(ps -o pgid= -p "$launch_pid" 2>/dev/null | tr -d ' ' || echo "?")"
    fi
    log_debug "stop_child launch_pid=${{launch_pid:-}} launch_pgid=$launch_pgid before $signal_name; will send to tracked_pids=[$tracked_pids_value] tracked_groups=[$tracked_groups_value]"
    log_snapshot_details "$(collect_tracking_snapshot)"
    log_debug "stop_child sending $signal_name to launch_pid=${{launch_pid:-}} tracked_pids=[$tracked_pids_value] tracked_groups=[$tracked_groups_value]"
    local pgid
    if [ -n "$tracked_groups_value" ]; then
        for pgid in $tracked_groups_value; do
            kill "-$signal_name" -- "-$pgid" >/dev/null 2>&1 || true
        done
    fi
    if [ -n "$tracked_pids_value" ]; then
        for pid in $tracked_pids_value; do
            kill "-$signal_name" "$pid" >/dev/null 2>&1 || true
        done
    fi
    if [ "${{adoption_observed:-0}}" -eq 1 ] || child_is_running; then
        kill "-$signal_name" -- "-$launch_pid" >/dev/null 2>&1 || kill "-$signal_name" "$launch_pid" >/dev/null 2>&1 || true
    else
        log_debug "stop_child skipping group kill to launch_pid=${{launch_pid:-}} because adoption_observed=${{adoption_observed:-0}} and launcher is not running"
    fi
    checks=$((exit_timeout_value * 10))
    if [ "$checks" -lt 1 ]; then
        checks=1
    fi

    for _ in $(seq 1 "$checks"); do
        if ! child_is_running; then
            log_debug "stop_child observed launcher exit before escalation"
            return 0
        fi
        sleep 0.1
    done

    log_debug "stop_child escalating to KILL for launch_pid=${{launch_pid:-}} tracked_pids=[$tracked_pids_value] tracked_groups=[$tracked_groups_value]"
    if [ -n "$tracked_groups_value" ]; then
        for pgid in $tracked_groups_value; do
            kill -KILL -- "-$pgid" >/dev/null 2>&1 || true
        done
    fi
    if [ -n "$tracked_pids_value" ]; then
        for pid in $tracked_pids_value; do
            kill -KILL "$pid" >/dev/null 2>&1 || true
        done
    fi
    if [ "${{adoption_observed:-0}}" -eq 1 ] || child_is_running; then
        kill -KILL -- "-$launch_pid" >/dev/null 2>&1 || kill -KILL "$launch_pid" >/dev/null 2>&1 || true
    else
        log_debug "stop_child skipping KILL group kill to launch_pid=${{launch_pid:-}} because adoption_observed=${{adoption_observed:-0}} and launcher is not running"
    fi
}}

snapshot_host_env() {{
    : > "$host_env_file"
    systemctl --user show-environment > "$systemd_env_dump"
    while IFS= read -r key; do
        grep -m1 "^${{key}}=" "$systemd_env_dump" >> "$host_env_file" || true
    done <<'EOF'
{chr(10).join(FLATPAK_PORTAL_ENV_KEYS)}
EOF
}}

import_portal_env() {{
    local -a names
    names=("$@")
    systemctl --user import-environment "${{names[@]}}" >/dev/null
    if command -v dbus-update-activation-environment >/dev/null 2>&1; then
        dbus-update-activation-environment --systemd "${{names[@]}}" >/dev/null 2>&1 || true
    fi
}}

restart_flatpak_portal() {{
    systemctl --user restart {FLATPAK_PORTAL_UNIT} >/dev/null
}}

apply_headless_portal_env() {{
    export DISPLAY="$display_value"
    export WAYLAND_DISPLAY="$wayland_value"
    export SWAYSOCK="{state['sway_socket']}"
    export DESKTOP_SESSION="sway"
    export XDG_CURRENT_DESKTOP="sway"
    export XDG_SESSION_DESKTOP="sway"
    export XDG_SESSION_TYPE="wayland"
    export PULSE_SINK="{audio_sink}"

    import_portal_env { " ".join(FLATPAK_PORTAL_ENV_KEYS) }
    restart_flatpak_portal
}}

restore_host_portal_env() {{
    local -a restore_names
    local -a absent_names
    local key
    local line
    local value

    restore_names=()
    absent_names=()

    while IFS= read -r key; do
        line="$(grep -m1 "^${{key}}=" "$host_env_file" || true)"
        if [ -n "$line" ]; then
            value="${{line#*=}}"
            export "${{key}}=${{value}}"
        else
            export "${{key}}="
            absent_names+=("$key")
        fi
        restore_names+=("$key")
    done <<'EOF'
{chr(10).join(FLATPAK_PORTAL_ENV_KEYS)}
EOF

    import_portal_env "${{restore_names[@]}}"
    if [ "${{#absent_names[@]}}" -gt 0 ]; then
        systemctl --user unset-environment "${{absent_names[@]}}" >/dev/null 2>&1 || true
    fi
    restart_flatpak_portal
}}

start_portal_monitor() {{
    monitor_log="$(mktemp "{PROFILE_ROOT}/portal-monitor-XXXXXX.log")"
    stdbuf -oL -eL gdbus monitor --session --dest org.freedesktop.portal.Flatpak --object-path /org/freedesktop/portal/Flatpak > "$monitor_log" 2>/dev/null &
    monitor_pid="$!"
    sleep 0.2
}}

wait_for_spawn() {{
    local checks
    checks=$((spawn_timeout * 10))
    for _ in $(seq 1 "$checks"); do
        if grep -q "SpawnStarted" "$monitor_log" 2>/dev/null; then
            return 0
        fi
        if [ -n "${{launch_pid:-}}" ] && ! kill -0 "$launch_pid" 2>/dev/null; then
            break
        fi
        sleep 0.1
    done
    return 1
}}

cleanup_monitor() {{
    if [ -n "${{monitor_pid:-}}" ]; then
        kill "$monitor_pid" >/dev/null 2>&1 || true
        wait "$monitor_pid" >/dev/null 2>&1 || true
        monitor_pid=""
    fi
    if [ -n "${{monitor_log:-}}" ]; then
        rm -f "$monitor_log"
        monitor_log=""
    fi
}}

host_env_file=""
systemd_env_dump=""
monitor_log=""
monitor_pid=""
launch_pid=""
portal_switched=0
launch_started=0
spawn_signal_observed=0
adoption_observed=0
launcher_exited_logged=0
portal_timeout_iterations=0

systemd_env_dump=""
monitor_log=""
monitor_pid=""
launch_pid=""
portal_switched=0
launch_started=0
launcher_exited_logged=0

cleanup() {{
    local exit_code=$?
    log_debug "cleanup exit_code=$exit_code launch_started=${{launch_started:-0}} portal_switched=${{portal_switched:-0}} spawn_signal_observed=${{spawn_signal_observed:-0}} adoption_observed=${{adoption_observed:-0}}"
    if [ "${{portal_switched:-0}}" -eq 1 ]; then
        restore_host_portal_env >/dev/null 2>&1 || true
    fi
    if [ "${{launch_started:-0}}" -eq 1 ]; then
        stop_child TERM >/dev/null 2>&1 || true
    fi
    cleanup_monitor
    rm -f "$host_env_file" "$systemd_env_dump" "$portal_active_file"
    exit "$exit_code"
}}

handle_signal() {{
    log_debug "wrapper received termination signal"
    stop_child TERM
}}

trap handle_signal INT TERM HUP

trap cleanup EXIT

if is_flatpak_command "$decoded_command"; then
    exec 9>"$portal_lock_file"
    if ! flock -w "$portal_timeout" 9; then
        echo "Another Flatpak virtual-display launch is already switching the portal environment." >&2
        exit 1
    fi

    host_env_file="$(mktemp "{PROFILE_ROOT}/portal-env-XXXXXX")"
    systemd_env_dump="$(mktemp "{PROFILE_ROOT}/systemd-env-XXXXXX")"
    printf 'pid=%s\\ncommand=%s\\nphase=portal-switch\\n' "$$" "$decoded_command" > "$portal_active_file"
    log_debug "starting transient Flatpak portal handoff"

    snapshot_host_env
    start_portal_monitor
    apply_headless_portal_env
    portal_switched=1
fi

launch_headless_direct "$decoded_command"
launch_started=1
write_active_state "launching"
echo "[LutrisToSunshine] Launch ID $launch_id started with outer PID $launch_pid" >&2
log_debug "launch_id=$launch_id outer_pid=$launch_pid"

if [ "${{portal_switched:-0}}" -eq 1 ]; then
    if ! wait_for_spawn; then
        log_debug "portal handoff timed out waiting for SpawnStarted; will continue watching for adoption evidence"
    else
        spawn_signal_observed=1
        log_debug "portal handoff SpawnStarted signal observed"
    fi
fi

idle_checks=0
post_launch_checks="$post_launch_grace_checks"
child_status=0
outer_child_status=0
outer_waited=0
while true; do
    snapshot_json="$(collect_tracking_snapshot)"
    if [ -z "$snapshot_json" ]; then
        snapshot_json='{{"launch_pids":[],"window_pids":[],"pgids":[]}}'
    fi
    update_tracking_state "$snapshot_json"
    if [ -n "$tracked_pids_value" ] && [ "${{adoption_observed:-0}}" -eq 0 ]; then
        adoption_observed=1
        log_debug "adoption_observed=1: first tracked processes detected"
    fi

    if [ "$snapshot_json" != "$last_snapshot_value" ]; then
        tracked_count="0"
        if [ -n "$tracked_pids_value" ]; then
            tracked_count="$(wc -w <<<"$tracked_pids_value" | awk '{{print $1}}')"
        fi
        echo "[LutrisToSunshine] Launch ID $launch_id tracking $tracked_count pid(s)" >&2
        log_pid_delta
        log_snapshot_details "$snapshot_json"
        log_debug "snapshot changed tracked_count=$tracked_count"
        last_snapshot_value="$snapshot_json"
        last_tracked_pids_value="$tracked_pids_value"
    fi

    write_active_state "running"

    if child_is_running; then
        idle_checks=0
    else
        if [ "$launcher_exited_logged" -eq 0 ]; then
            if [ "$outer_waited" -eq 0 ]; then
                set +e
                wait "$launch_pid"
                outer_child_status=$?
                set -e
                outer_waited=1
            fi
            echo "[LutrisToSunshine] Launch ID $launch_id outer launcher exited; adopting remaining processes" >&2
            log_debug "outer launcher exited with status=$outer_child_status"
            launcher_exited_logged=1
        fi
        if [ -n "$tracked_pids_value" ]; then
            idle_checks=0
        else
            # Abort if we have a Flatpak portal handoff but neither spawn signal nor adoption appeared
            # This prevents waiting indefinitely when the portal handoff silently fails
            if [ "${{portal_switched:-0}}" -eq 1 ] && [ "${{spawn_signal_observed:-0}}" -eq 0 ] && [ "${{adoption_observed:-0}}" -eq 0 ] && [ "${{launcher_exited_logged:-0}}" -eq 1 ]; then
                portal_timeout_iterations=$((portal_timeout_iterations + 1))
                portal_timeout_max=$((spawn_timeout * 2))
                if [ "$portal_timeout_iterations" -ge "$portal_timeout_max" ]; then
                    log_debug "aborting: neither SpawnStarted signal nor adoption detected within ${{spawn_timeout}}s timeout after portal handoff and launcher exit"
                    echo "Flatpak portal handoff failed: no SpawnStarted signal or adopted processes detected within timeout." >&2
                    exit 1
                fi
            fi
            if [ "$post_launch_checks" -gt 0 ]; then
                post_launch_checks=$((post_launch_checks - 1))
            else
                idle_checks=$((idle_checks + 1))
                log_debug "no tracked processes remain; idle_checks=$idle_checks"
                if [ "$idle_checks" -ge "$adoption_exit_grace_checks" ]; then
                    break
                fi
            fi
        fi
    fi

    sleep "$adoption_poll_interval"
done

if child_is_running; then
    set +e
    wait "$launch_pid"
    child_status=$?
    set -e
    log_debug "wrapper exiting after launcher process ended with status=$child_status"
elif [ "$launcher_exited_logged" -eq 1 ]; then
    child_status=0
    log_debug "wrapper exiting after adopted processes disappeared; outer_status=$outer_child_status"
fi
launch_started=0

# Explicit portal restoration on normal completion path
if [ "${{portal_switched:-0}}" -eq 1 ]; then
    restore_host_portal_env >/dev/null 2>&1 || true
fi
rm -f "$portal_active_file"
portal_switched=0
trap - EXIT INT TERM HUP
cleanup_monitor
rm -f "$host_env_file" "$systemd_env_dump"
log_debug "wrapper final exit status=$child_status"
exit "$child_status"
""",
        Path(paths["set_resolution_script"]): f"""#!/bin/bash
set -euo pipefail

if [ ! -S "{state['sway_socket']}" ]; then
    exit 0
fi

if [ -z "${{SUNSHINE_CLIENT_WIDTH:-}}" ] || [ -z "${{SUNSHINE_CLIENT_HEIGHT:-}}" ] || [ -z "${{SUNSHINE_CLIENT_FPS:-}}" ]; then
    exit 0
fi

SWAYSOCK="{state['sway_socket']}" swaymsg "output HEADLESS-1 mode ${{SUNSHINE_CLIENT_WIDTH}}x${{SUNSHINE_CLIENT_HEIGHT}}@${{SUNSHINE_CLIENT_FPS}}Hz" >/dev/null 2>&1 || true
# Removed sleep after resolution change for faster startup
""",
        Path(paths["reset_resolution_script"]): f"""#!/bin/bash
set -euo pipefail

if [ ! -S "{state['sway_socket']}" ]; then
    exit 0
fi

SWAYSOCK="{state['sway_socket']}" swaymsg "output HEADLESS-1 mode {FALLBACK_WIDTH}x{FALLBACK_HEIGHT}@{FALLBACK_FPS}Hz" >/dev/null 2>&1 || true
""",
    }


def _systemd_templates(state: Dict[str, Any]) -> Dict[Path, str]:
    paths = state["paths"]
    return {
        Path(paths["sunshine_override"]): f"""[Service]
ExecStart=
ExecStart={paths['sunshine_wrapper_script']}
""",
    }


def _udev_rule() -> str:
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
    return f"""# Managed by LutrisToSunshine virtualdisplay.
ACTION=="add|change", SUBSYSTEM=="input", ATTRS{{id/vendor}}=="{SUNSHINE_INPUT_VENDOR_ID:04x}", ATTRS{{id/product}}=="{SUNSHINE_INPUT_PRODUCT_ID:04x}", {sunshine_input_clause}
ACTION!="remove", KERNEL=="uhid", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uhid"
"""


def _install_udev_rule(state: Dict[str, Any]) -> bool:
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
        handle.write(_udev_rule())
        temp_path = handle.name
    try:
        ok = _run_privileged(["install", "-D", "-m", "0644", temp_path, state["udev_rule_path"]])
        if not ok:
            return False
        return _reload_udev_rules()
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def _remove_udev_rule(state: Dict[str, Any]) -> bool:
    ok = _run_privileged(["rm", "-f", state["udev_rule_path"]])
    if not ok:
        return False
    return _reload_udev_rules()


def _ensure_dependencies() -> List[str]:
    missing = []
    for binary in ["flock", "gdbus", "pactl", "python3", "setfacl", "stdbuf", "sway", "swaybg", "swaymsg", "systemctl"]:
        if shutil.which(binary) is None:
            missing.append(binary)
    if _sunshine_binary() is None:
        missing.append("sunshine")
    evdev_error = _evdev_import_error()
    if evdev_error:
        missing.append("python-evdev")
    return missing


def _write_managed_files(state: Dict[str, Any]) -> None:
    PROFILE_ROOT.mkdir(parents=True, exist_ok=True)
    BIN_ROOT.mkdir(parents=True, exist_ok=True)
    Path(state["paths"]["systemd_user_dir"]).mkdir(parents=True, exist_ok=True)

    for path, content in _script_templates(state).items():
        _write_file(path, content, executable=path.suffix == ".sh")
    for path, content in _systemd_templates(state).items():
        _write_file(path, content)


def refresh_managed_files(state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if state is None:
        state = load_state()
    _write_managed_files(state)
    save_state(state)
    _daemon_reload()
    return state


def _restore_sunshine_audio_sink(state: Dict[str, Any]) -> None:
    sunshine_conf = Path(state["paths"]["sunshine_conf"])
    original = state.get("sunshine_audio_sink", {"present": False, "value": ""})
    if original.get("present"):
        _set_key_value(sunshine_conf, "audio_sink", original.get("value", ""))
    else:
        _remove_key(sunshine_conf, "audio_sink")


def _legacy_unit_names() -> List[str]:
    return [
        LEGACY_INPUT_BRIDGE_UNIT,
        LEGACY_AUDIO_GUARD_UNIT,
        LEGACY_SUNSHINE_UNIT,
        LEGACY_SWAY_UNIT,
    ]


def _legacy_unit_paths(state: Dict[str, Any]) -> List[Path]:
    paths = state["paths"]
    return [
        Path(paths["legacy_input_bridge_unit"]),
        Path(paths["legacy_audio_guard_unit"]),
        Path(paths["legacy_sunshine_unit"]),
        Path(paths["legacy_sway_unit"]),
    ]


def _cleanup_legacy_virtualdisplay_units(state: Dict[str, Any]) -> None:
    for unit in _legacy_unit_names():
        _systemctl_user("stop", unit)
        _systemctl_user("disable", unit)
    for path in _legacy_unit_paths(state):
        try:
            path.unlink()
        except OSError:
            pass


def _sunshine_service_active() -> bool:
    return _systemctl_user("is-active", SUNSHINE_UNIT).returncode == 0


def _daemon_reload() -> None:
    _systemctl_user("daemon-reload")


def _clear_input_bridge_status_file(state: Dict[str, Any]) -> None:
    try:
        Path(state["paths"]["input_bridge_status_file"]).unlink()
    except OSError:
        pass


def _bridge_runtime_enabled(state: Dict[str, Any]) -> bool:
    return bool(state.get("enabled") and _has_selected_input_devices(state))


def _bridge_service_state() -> str:
    if Path(load_state()["paths"]["input_bridge_status_file"]).exists():
        return "active"
    if _sunshine_service_active() and _has_selected_input_devices(load_state()):
        return "starting"
    return "inactive"


def _apply_input_bridge_runtime_state(state: Dict[str, Any]) -> None:
    if not _bridge_runtime_enabled(state):
        _clear_input_bridge_status_file(state)
        return

    if not _sunshine_service_active():
        return

    # The input bridge now runs as a child of the Sunshine override wrapper.
    # Restart the service so the wrapper can re-read the saved controller selection.
    _systemctl_user("restart", SUNSHINE_UNIT)


def _parse_selection_numbers(value: str, total_items: int) -> List[int]:
    value = value.strip()
    if value in {"", "0", "none"}:
        return []

    indices = []
    for part in value.split(","):
        token = part.strip()
        if not token:
            raise ValueError()
        if "-" in token:
            start_raw, end_raw = token.split("-", 1)
            start = int(start_raw)
            end = int(end_raw)
            if start > end:
                raise ValueError()
            indices.extend(range(start - 1, end))
            continue
        indices.append(int(token) - 1)

    unique_indices = sorted(set(indices))
    if all(0 <= index < total_items for index in unique_indices):
        return unique_indices
    raise ValueError()


def _parse_selection_toggle_numbers(value: str, total_items: int) -> Optional[List[int]]:
    value = value.strip()
    if value == "":
        return None
    return _parse_selection_numbers(value, total_items)


def _toggle_selection_entries(
    selections: List[Dict[str, Any]],
    devices: List[Dict[str, Any]],
    toggled_indices: List[int],
) -> List[Dict[str, Any]]:
    updated = [_normalized_selection_entry(selection) for selection in selections]
    for index in toggled_indices:
        device = devices[index]
        existing_index = next(
            (position for position, selection in enumerate(updated) if _selection_matches_device(selection, device)),
            None,
        )
        if existing_index is not None:
            updated.pop(existing_index)
            continue
        updated.append(
            _normalized_selection_entry(
                {
                    "selection_id": device["selection_id"],
                    "label": device["label"],
                    "fingerprint": device["fingerprint"],
                }
            )
        )
    return updated


def configure_exclusive_input_devices() -> int:
    state = load_state()
    selections = state["exclusive_input_devices"]["devices"]
    devices, error = _list_controller_devices()
    if error:
        print(error)
        return 1

    if not devices:
        if selections:
            print("No eligible host controllers are currently connected.")
            print("Saved exclusive controllers:")
            for selection in selections:
                print(f"- {selection['label']}")
            clear_saved = get_user_input(
                "Enter 0 to clear saved controller selections, or press Ctrl+C to cancel: ",
                lambda raw: raw.strip() if raw.strip() == "0" else (_ for _ in ()).throw(ValueError()),
                "Invalid selection. Enter 0 to clear the saved controller selections.",
            )
            state["exclusive_input_devices"] = _empty_exclusive_input_state()
            save_state(state)
            _apply_input_bridge_runtime_state(state)
            print("Exclusive host controller routing cleared.")
            return 0

        print("No eligible host controllers are currently connected.")
        return 0

    print("Connected host controllers:")
    for index, device in enumerate(devices, start=1):
        selected = " [selected]" if _device_matches_any_selection(device, selections) else ""
        print(f"{index}. {device['label']}{selected}")

    toggled_indices = get_user_input(
        "Toggle controller numbers for exclusive routing (comma-separated or ranges), press Enter to keep, or 0 to clear: ",
        lambda raw: _parse_selection_toggle_numbers(raw, len(devices)),
        "Invalid selection. Please use comma-separated numbers or ranges such as 1,3-4.",
    )
    if toggled_indices is None:
        print("Exclusive host controller routing unchanged.")
        return 0

    state["exclusive_input_devices"] = {
        "devices": _toggle_selection_entries(selections, devices, toggled_indices)
    }
    state = refresh_managed_files(state)
    _apply_input_bridge_runtime_state(state)

    selected_devices = state["exclusive_input_devices"]["devices"]
    if selected_devices:
        print(f"Saved {len(selected_devices)} exclusive host controller(s).")
    else:
        print("Exclusive host controller routing cleared.")
    return 0


def setup_virtual_display() -> int:
    missing = _ensure_dependencies()
    if missing:
        print("Missing required commands:", ", ".join(missing))
        return 1

    state = load_state()
    sunshine_was_active = _sunshine_service_active()
    state = refresh_managed_files(state)
    _remember_sunshine_audio_sink(state)
    save_state(state)

    if not _install_udev_rule(state):
        _restore_sunshine_audio_sink(state)
        state["sunshine_audio_sink"] = None
        save_state(state)
        print("Error: unable to install the Sunshine input isolation udev rule.")
        print("Install sudo or pkexec, then rerun the command.")
        return 1

    _cleanup_legacy_virtualdisplay_units(state)
    _daemon_reload()
    state["enabled"] = True
    save_state(state)
    if sunshine_was_active:
        _systemctl_user("restart", SUNSHINE_UNIT)

    print("Virtual display files installed.")
    return 0


def start_virtual_display() -> int:
    state = load_state()
    if not state.get("enabled"):
        print("Virtual display is not set up. Run 'virtualdisplay setup' first.")
        return 1
    state = refresh_managed_files(state)
    _remember_sunshine_audio_sink(state)
    save_state(state)
    result = _systemctl_user("start", SUNSHINE_UNIT)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        _systemctl_user("stop", SUNSHINE_UNIT)
        _restore_sunshine_audio_sink(state)
        _restore_host_audio_defaults(state)
        save_state(state)
        if stderr:
            print(stderr)
        print("Error: unable to start the virtual display Sunshine service.")
        return 1
    print("Virtual display started.")
    return 0


def stop_virtual_display() -> int:
    state = load_state()
    if not state.get("enabled"):
        print("Virtual display is not set up.")
        return 1
    _systemctl_user("stop", SUNSHINE_UNIT)
    _clear_input_bridge_status_file(state)
    _restore_sunshine_audio_sink(state)
    _restore_host_audio_defaults(state)
    save_state(state)
    print("Virtual display stopped.")
    return 0


def virtual_display_status() -> int:
    state = load_state()
    if not state.get("enabled"):
        print("Virtual display is not configured.")
        return 1

    sunshine_active = _sunshine_service_active()
    sway_active = sunshine_active and Path(state["sway_socket"]).exists() and WAYLAND_DISPLAY_PATH.exists()
    bridge_state = _bridge_service_state()
    wayland_display = ""
    if WAYLAND_DISPLAY_PATH.exists():
        wayland_display = WAYLAND_DISPLAY_PATH.read_text(encoding="utf-8").strip()
    portal_handoff_active = PORTAL_ACTIVE_PATH.exists()
    audio_guard_state = "active" if sunshine_active and Path(state["paths"]["audio_module_file"]).exists() else "inactive"

    print(f"Profile: {state['profile']}")
    print(f"Managed Sunshine unit: {SUNSHINE_UNIT} ({'active' if sunshine_active else 'inactive'})")
    print(f"Headless sway runtime: {'active' if sway_active else 'inactive'}")
    print(f"Input bridge runtime: {bridge_state}")
    print(f"Audio guard runtime: {audio_guard_state}")
    print(f"Audio sink: {state['audio_sink']}")
    print(f"Sunshine audio target: {_sunshine_audio_capture_target(state)}")
    host_defaults = state.get("host_audio_defaults") or {}
    if host_defaults.get("sink") or host_defaults.get("source"):
        print(
            "Saved host audio defaults: "
            f"sink={host_defaults.get('sink') or 'unset'}, "
            f"source={host_defaults.get('source') or 'unset'}"
        )
    print(f"Headless Wayland display: {wayland_display or 'not detected'}")
    print(f"Sway socket: {state['sway_socket']}")
    print(f"Udev rule: {state['udev_rule_path']}")
    print("Flatpak launch mode: transient portal handoff")
    print(f"Portal handoff: {'active' if portal_handoff_active else 'idle'}")
    print(f"Last launch log: {state['paths']['last_launch_log_file']}")
    selections = state["exclusive_input_devices"]["devices"]
    if not selections:
        print("Exclusive host controllers: none configured")
    else:
        devices, error = _list_controller_devices()
        runtime_status = _input_bridge_status(state)
        runtime_by_id = {}
        for item in runtime_status.get("devices", []):
            if isinstance(item, dict) and item.get("selection_id"):
                runtime_by_id[item["selection_id"]] = item
        if error:
            print(f"Exclusive host controllers: unavailable ({error})")
        else:
            print("Exclusive host controllers:")
            for selection in selections:
                runtime = runtime_by_id.get(selection["selection_id"], {})
                current_match = next(
                    (device for device in devices if _selection_matches_device(selection, device)),
                    None,
                )
                if runtime:
                    message = runtime.get("message", "").strip()
                    identity_bits = _selection_runtime_identity_bits(runtime)
                    details = f": {message}" if message else ""
                    suffix = f" [{' | '.join(identity_bits)}]" if identity_bits else ""
                    print(f"- {selection['label']} ({runtime.get('state', 'unknown')}){suffix}{details}")
                elif current_match:
                    print(f"- {selection['label']} (detected at {current_match['event_path']})")
                else:
                    print(f"- {selection['label']} (missing)")
    print("Sunshine capture settings are user-managed and unchanged by this command.")
    return 0


def virtual_display_logs(lines: int = 80) -> int:
    result = subprocess.run(
        [
            "journalctl",
            "--user",
            "-u",
            SUNSHINE_UNIT,
            "-n",
            str(lines),
            "--no-pager",
        ],
        check=False,
    )
    return result.returncode


def remove_virtual_display() -> int:
    state = load_state()
    if not state.get("enabled"):
        print("Virtual display is not configured.")
        return 1

    stop_virtual_display()
    _cleanup_legacy_virtualdisplay_units(state)

    if not _remove_udev_rule(state):
        print("Warning: failed to remove the managed udev rule.")

    for path_key in [
        "sunshine_override",
        "input_bridge_script",
        "audio_guard_script",
        "sunshine_wrapper_script",
    ]:
        try:
            Path(state["paths"][path_key]).unlink()
        except OSError:
            pass
    try:
        Path(state["paths"]["sunshine_override_dir"]).rmdir()
    except OSError:
        pass
    for path_key in [
        "portal_active_file",
        "portal_lock_file",
        "input_bridge_status_file",
        "wayland_display_file",
        "audio_module_file",
    ]:
        try:
            Path(state["paths"][path_key]).unlink()
        except OSError:
            pass

    state["enabled"] = False
    save_state(state)
    _daemon_reload()

    print("Virtual display removed.")
    return 0
