import base64
import glob
import grp
import hashlib
import json
import os
import pwd
import re
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
LEGACY_DISPLAY_DIRNAME = "virtual" + "display"
DISPLAY_ROOT = CONFIG_ROOT / "display"
LEGACY_DISPLAY_ROOT = CONFIG_ROOT / LEGACY_DISPLAY_DIRNAME
PROFILE_ROOT = DISPLAY_ROOT / PROFILE_NAME
BIN_ROOT = CONFIG_ROOT / "bin"
DISPLAY_STATE_PATH = DISPLAY_ROOT / "display.json"
LEGACY_STATE_PATH = LEGACY_DISPLAY_ROOT / f"{LEGACY_DISPLAY_DIRNAME}.json"
SUNSHINE_UNIT = "app-dev.lizardbyte.app.Sunshine.service"
FALLBACK_SUNSHINE_UNIT = "sunshine.service"
FLATPAK_PORTAL_UNIT = "flatpak-portal.service"
DISPLAY_SOCKET_PATH = f"/run/user/{os.getuid()}/lutristosunshine-display.sock"
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
REFRESH_RATE_SYNC_MODES = {"client", "exact", "custom"}
SUNSHINE_FLATPAK_ID = "dev.lizardbyte.app.Sunshine"
SUNSHINE_INPUT_VENDOR_ID = 0xBEEF
SUNSHINE_INPUT_PRODUCT_ID = 0xDEAD
SUNSHINE_INPUT_NAME_MARKERS = [
    "Keyboard_passthrough",
    "Mouse_passthrough",
    "Touch_passthrough",
    "Pen_passthrough",
]
BRIDGE_DEVICE_PHYS_PREFIX = "lts-inputbridge/"
HIDRAW_BUFFER_MAX = 4096
AUDIO_GUARD_POLL_INTERVAL_SECONDS = 0.5
HEADLESS_PREP_PREFIX = "headless:"
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


def _normalized_refresh_rate_sync_mode(value: Any) -> str:
    normalized = _safe_string(value).lower()
    if normalized in REFRESH_RATE_SYNC_MODES:
        return normalized
    return "client"


def _normalized_custom_display_mode(value: Any) -> Dict[str, Any]:
    default_mode = {
        "width": FALLBACK_WIDTH,
        "height": FALLBACK_HEIGHT,
        "refresh": float(FALLBACK_FPS),
    }
    if not isinstance(value, dict):
        return default_mode

    try:
        width = int(value.get("width", default_mode["width"]))
    except (TypeError, ValueError):
        width = default_mode["width"]
    try:
        height = int(value.get("height", default_mode["height"]))
    except (TypeError, ValueError):
        height = default_mode["height"]
    try:
        refresh = float(value.get("refresh", default_mode["refresh"]))
    except (TypeError, ValueError):
        refresh = default_mode["refresh"]

    if width <= 0:
        width = default_mode["width"]
    if height <= 0:
        height = default_mode["height"]
    if refresh <= 0:
        refresh = default_mode["refresh"]

    return {
        "width": width,
        "height": height,
        "refresh": refresh,
    }


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


def _active_launch_status(state: Dict[str, Any]) -> Dict[str, str]:
    portal_active_path = Path(state["paths"]["portal_active_file"])
    if not portal_active_path.exists():
        return {}
    try:
        lines = portal_active_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    payload: Dict[str, str] = {}
    for line in lines:
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = _safe_string(key)
        if key:
            payload[key] = value.strip()
    return payload


def _format_refresh_rate_hz(refresh_value: Any) -> str:
    try:
        refresh = float(refresh_value)
    except (TypeError, ValueError):
        return ""
    if refresh <= 0:
        return ""
    if refresh > 1000:
        refresh /= 1000.0
    nearest = round(refresh)
    if abs(refresh - nearest) < 0.01:
        return str(int(nearest))
    return f"{refresh:.2f}"


def _current_headless_mode(state: Dict[str, Any], sunshine_active: bool, sway_active: bool) -> str:
    if not state.get("enabled") or not sunshine_active or not sway_active:
        return ""
    sway_socket = _safe_string(state.get("sway_socket"))
    if not sway_socket or not Path(sway_socket).exists():
        return ""
    result = _run(
        ["swaymsg", "-s", sway_socket, "-t", "get_outputs", "-r"],
        check=False,
    )
    if result.returncode != 0 or not _safe_string(result.stdout):
        return ""
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, list):
        return ""
    for output in payload:
        if not isinstance(output, dict) or _safe_string(output.get("name")) != "HEADLESS-1":
            continue
        current_mode = output.get("current_mode")
        if not isinstance(current_mode, dict):
            return ""
        width = current_mode.get("width")
        height = current_mode.get("height")
        refresh_hz = _format_refresh_rate_hz(current_mode.get("refresh"))
        if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0 or not refresh_hz:
            return ""
        return f"{width}x{height} @ {refresh_hz} Hz"
    return ""


def _custom_display_mode_string(value: Any) -> str:
    mode = _normalized_custom_display_mode(value)
    refresh_hz = _format_refresh_rate_hz(mode["refresh"])
    if not refresh_hz:
        return ""
    return f"{mode['width']}x{mode['height']} @ {refresh_hz} Hz"


def _state_paths() -> Dict[str, str]:
    systemd_user_dir = Path("~/.config/systemd/user").expanduser()
    override_dir = systemd_user_dir / f"{_sunshine_unit()}.d"
    return {
        "profile_root": str(PROFILE_ROOT),
        "bin_root": str(BIN_ROOT),
        "state_path": str(DISPLAY_STATE_PATH),
        "sway_config": str(PROFILE_ROOT / "sway.conf"),
        "sway_start_script": str(BIN_ROOT / "lutristosunshine-start-headless-sway.sh"),
        "sunshine_start_script": str(BIN_ROOT / "lutristosunshine-start-display-sunshine.sh"),
        "sunshine_wrapper_script": str(BIN_ROOT / "lutristosunshine-run-display-service.sh"),
        "audio_create_script": str(BIN_ROOT / "lutristosunshine-create-audio-sink.sh"),
        "audio_cleanup_script": str(BIN_ROOT / "lutristosunshine-cleanup-audio-sink.sh"),
        "audio_guard_script": str(BIN_ROOT / "lutristosunshine-guard-audio-defaults.sh"),
        "launch_app_script": str(BIN_ROOT / "lutristosunshine-launch-app.sh"),
        "resolve_stream_fps_script": str(BIN_ROOT / "lutristosunshine-resolve-stream-fps.sh"),
        "apply_exact_refresh_script": str(BIN_ROOT / "lutristosunshine-apply-exact-refresh.sh"),
        "headless_prep_script": str(BIN_ROOT / "lutristosunshine-run-headless-prep.sh"),
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
        "input_bridge_script": str(BIN_ROOT / "lutristosunshine-input-bridge.py"),
        "kwin_input_isolation_script": str(BIN_ROOT / "lutristosunshine-kwin-input-isolation.py"),
        "sunshine_conf": str(detect_sunshine_config_root() / "sunshine.conf"),
        "kwin_input_isolation_status_file": str(PROFILE_ROOT / "kwin-input-isolation-status.json"),
    }


def _default_state() -> Dict[str, Any]:
    paths = _state_paths()
    return {
        "enabled": False,
        "dynamic_mangohud_fps_limit": False,
        "refresh_rate_sync_mode": "client",
        "custom_display_mode": _normalized_custom_display_mode(None),
        "sunshine_execstart": "",
        "sunshine_unit_name": "",
        "profile": PROFILE_NAME,
        "audio_sink": "lts-sunshine-stereo",
        "host_audio_defaults": {"sink": "", "source": ""},
        "sway_socket": DISPLAY_SOCKET_PATH,
        "udev_rule_path": UDEV_RULE_PATH,
        "sunshine_audio_sink": None,
        "exclusive_input_devices": _empty_exclusive_input_state(),
        "paths": paths,
    }


def load_state() -> Dict[str, Any]:
    state_path = DISPLAY_STATE_PATH if DISPLAY_STATE_PATH.exists() else LEGACY_STATE_PATH
    if not state_path.exists():
        return _default_state()
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_state()
    state = _default_state()
    state.update(data)
    state["refresh_rate_sync_mode"] = _normalized_refresh_rate_sync_mode(
        state.get("refresh_rate_sync_mode")
    )
    state["custom_display_mode"] = _normalized_custom_display_mode(
        state.get("custom_display_mode")
    )
    state["exclusive_input_devices"] = _normalized_exclusive_input_state(
        state.get("exclusive_input_devices")
    )
    state["paths"] = _state_paths()
    return state


def save_state(state: Dict[str, Any]) -> None:
    DISPLAY_ROOT.mkdir(parents=True, exist_ok=True)
    state["exclusive_input_devices"] = _normalized_exclusive_input_state(
        state.get("exclusive_input_devices")
    )
    state["custom_display_mode"] = _normalized_custom_display_mode(
        state.get("custom_display_mode")
    )
    DISPLAY_STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    if LEGACY_STATE_PATH.exists():
        try:
            LEGACY_STATE_PATH.unlink()
        except OSError:
            pass


def is_enabled() -> bool:
    return bool(load_state().get("enabled"))


def dynamic_mangohud_fps_limit_enabled() -> bool:
    return bool(load_state().get("dynamic_mangohud_fps_limit"))


def refresh_rate_sync_mode() -> str:
    return _normalized_refresh_rate_sync_mode(load_state().get("refresh_rate_sync_mode"))


def custom_display_mode() -> Dict[str, Any]:
    return _normalized_custom_display_mode(load_state().get("custom_display_mode"))


def set_dynamic_mangohud_fps_limit(enabled: bool) -> Dict[str, Any]:
    state = load_state()
    state["dynamic_mangohud_fps_limit"] = bool(enabled)
    return refresh_managed_files(state)


def set_refresh_rate_sync_mode(mode: str) -> Dict[str, Any]:
    state = load_state()
    state["refresh_rate_sync_mode"] = _normalized_refresh_rate_sync_mode(mode)
    return refresh_managed_files(state)


def set_custom_display_mode(width: int, height: int, refresh: float) -> Dict[str, Any]:
    state = load_state()
    state["custom_display_mode"] = _normalized_custom_display_mode(
        {
            "width": width,
            "height": height,
            "refresh": refresh,
        }
    )
    return refresh_managed_files(state)


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


def get_headless_prep_script() -> str:
    return load_state()["paths"]["headless_prep_script"]


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


def analyze_flatpak_command_for_display(command: Optional[str]) -> Optional[str]:
    if not command:
        return None
    parsed_command, error = _parse_flatpak_run_command(command)
    if parsed_command is None:
        return error
    return None


def _systemctl_user(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return _run(["systemctl", "--user", *args], check=check)


def _parse_systemd_execstart(value: str) -> str:
    raw_value = _safe_string(value)
    if not raw_value:
        return ""

    for line in raw_value.splitlines():
        match = re.search(r"argv\[]=(.*?) ;", line)
        if match:
            return _safe_string(match.group(1))

    if "argv[]=" in raw_value:
        match = re.search(r"argv\[]=(.*)", raw_value, re.DOTALL)
        if match:
            return _safe_string(match.group(1))

    return raw_value


def _current_sunshine_execstart(unit: str) -> str:
    result = _systemctl_user("show", "--property=ExecStart", "--value", unit)
    if result.returncode != 0:
        return ""
    return _parse_systemd_execstart(result.stdout or "")


def _fragment_sunshine_execstart(unit: str) -> str:
    fragment_result = _systemctl_user("show", "--property=FragmentPath", "--value", unit)
    if fragment_result.returncode != 0:
        return ""

    fragment_path = Path(_safe_string(fragment_result.stdout))
    if not fragment_path.is_file():
        return ""

    in_service_section = False
    try:
        for raw_line in fragment_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("[") and line.endswith("]"):
                in_service_section = line == "[Service]"
                continue
            if not in_service_section or not line.startswith("ExecStart="):
                continue
            execstart = _safe_string(line.split("=", 1)[1])
            if execstart:
                return execstart
    except OSError:
        return ""

    return ""


def _remember_sunshine_execstart(state: Dict[str, Any], unit: Optional[str] = None) -> Dict[str, Any]:
    target_unit = unit or _sunshine_unit()
    current_execstart = _current_sunshine_execstart(target_unit) or _fragment_sunshine_execstart(target_unit)
    wrapper_path = _safe_string(state.get("paths", {}).get("sunshine_wrapper_script"))

    if current_execstart and wrapper_path and current_execstart != wrapper_path:
        state["sunshine_execstart"] = current_execstart
        state["sunshine_unit_name"] = target_unit
        return state

    if _safe_string(state.get("sunshine_execstart")):
        state["sunshine_unit_name"] = target_unit
        return state

    fallback_execstart = _sunshine_binary() or "sunshine"
    state["sunshine_execstart"] = fallback_execstart
    state["sunshine_unit_name"] = target_unit
    return state


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
    probe_env = dict(os.environ)
    probe_env["LANG"] = "C"
    probe_env["LC_ALL"] = "C"
    result = subprocess.run(
        ["pactl", "info"],
        text=True,
        capture_output=True,
        check=False,
        env=probe_env,
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
    if not current_sink or not current_source:
        return
    if current_sink in _managed_audio_sink_names(state) or current_source in _managed_audio_source_names(state):
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


def _is_plasma_session() -> bool:
    current_desktop = _safe_string(os.environ.get("XDG_CURRENT_DESKTOP")).lower()
    session_desktop = _safe_string(os.environ.get("XDG_SESSION_DESKTOP")).lower()
    desktop_session = _safe_string(os.environ.get("DESKTOP_SESSION")).lower()
    kde_full_session = _safe_string(os.environ.get("KDE_FULL_SESSION")).lower()
    plasma_markers = ("kde", "plasma", "kwin")
    return (
        kde_full_session in {"1", "true", "yes", "on"}
        or any(marker in current_desktop for marker in plasma_markers)
        or any(marker in session_desktop for marker in plasma_markers)
        or any(marker in desktop_session for marker in plasma_markers)
    )


def _host_session_name() -> str:
    parts = []
    for value in [
        _safe_string(os.environ.get("XDG_CURRENT_DESKTOP")),
        _safe_string(os.environ.get("XDG_SESSION_DESKTOP")),
        _safe_string(os.environ.get("DESKTOP_SESSION")),
    ]:
        if value and value not in parts:
            parts.append(value)
    if parts:
        return " / ".join(parts)
    if _safe_string(os.environ.get("KDE_FULL_SESSION")).lower() in {"1", "true", "yes", "on"}:
        return "KDE"
    return "unknown"


def _input_isolation_mode() -> str:
    return "kwin-runtime-disable" if _is_plasma_session() else "permissions-only"


def _empty_kwin_input_isolation_status() -> Dict[str, Any]:
    return {
        "state": "inactive",
        "service": "",
        "disabled_devices": [],
        "failed_devices": [],
        "seen_device_count": 0,
        "last_error": "",
    }


def _kwin_input_isolation_status(state: Dict[str, Any]) -> Dict[str, Any]:
    status = _empty_kwin_input_isolation_status()
    path = Path(state["paths"]["kwin_input_isolation_status_file"])
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
    status["state"] = _safe_string(status.get("state")) or "inactive"
    status["service"] = _safe_string(status.get("service"))
    status["last_error"] = _safe_string(status.get("last_error"))
    try:
        status["seen_device_count"] = int(status.get("seen_device_count") or 0)
    except (TypeError, ValueError):
        status["seen_device_count"] = 0
    return status


def _sunshine_virtual_input_devices() -> List[Dict[str, str]]:
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
            vendor_id = _safe_string(current.get("vendor_id")).lower()
            product_id = _safe_string(current.get("product_id")).lower()
            if vendor_id == f"{SUNSHINE_INPUT_VENDOR_ID:04x}" and product_id == f"{SUNSHINE_INPUT_PRODUCT_ID:04x}":
                devices.append(
                    {
                        "name": _safe_string(current.get("name")),
                        "event_path": _safe_string(current.get("event_path")),
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
        vendor_id = _safe_string(current.get("vendor_id")).lower()
        product_id = _safe_string(current.get("product_id")).lower()
        if vendor_id == f"{SUNSHINE_INPUT_VENDOR_ID:04x}" and product_id == f"{SUNSHINE_INPUT_PRODUCT_ID:04x}":
            devices.append(
                {
                    "name": _safe_string(current.get("name")),
                    "event_path": _safe_string(current.get("event_path")),
                }
            )
    return devices


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


def _kwin_input_isolation_script(state: Dict[str, Any]) -> str:
    python_executable = sys.executable or "/usr/bin/env python3"
    return f"""#!{python_executable}
import json
import os
import re
import signal
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

STATUS_PATH = Path({state["paths"]["kwin_input_isolation_status_file"]!r})
SERVICE_CANDIDATES = ["org.kde.KWin", "org.kde.KWin.InputDevice"]
ROOT_PATHS = ["/org/kde/KWin/InputDevice", "/org/kde/KWin"]
DEVICE_INTERFACE = "org.kde.KWin.InputDevice"
SUNSHINE_VENDOR_ID = {SUNSHINE_INPUT_VENDOR_ID}
SUNSHINE_PRODUCT_ID = {SUNSHINE_INPUT_PRODUCT_ID}
NAME_MARKERS = {SUNSHINE_INPUT_NAME_MARKERS!r}
STOP = False


def safe_string(value):
    return str(value or "").strip()


def is_plasma_session():
    current_desktop = safe_string(os.environ.get("XDG_CURRENT_DESKTOP")).lower()
    session_desktop = safe_string(os.environ.get("XDG_SESSION_DESKTOP")).lower()
    desktop_session = safe_string(os.environ.get("DESKTOP_SESSION")).lower()
    kde_full_session = safe_string(os.environ.get("KDE_FULL_SESSION")).lower()
    plasma_markers = ("kde", "plasma", "kwin")
    return (
        kde_full_session in {{"1", "true", "yes", "on"}}
        or any(marker in current_desktop for marker in plasma_markers)
        or any(marker in session_desktop for marker in plasma_markers)
        or any(marker in desktop_session for marker in plasma_markers)
    )


def write_status(state="inactive", service="", disabled_devices=None, failed_devices=None, seen_device_count=0, last_error=""):
    payload = {{
        "state": state,
        "service": service,
        "disabled_devices": disabled_devices or [],
        "failed_devices": failed_devices or [],
        "seen_device_count": seen_device_count,
        "last_error": safe_string(last_error),
    }}
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def handle_signal(_signum, _frame):
    global STOP
    STOP = True


def run_gdbus(*args):
    result = subprocess.run(
        ["gdbus", *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(safe_string(result.stderr) or f"gdbus exit {{result.returncode}}")
    stdout = result.stdout.strip()
    if not stdout:
        raise RuntimeError("gdbus returned no output")
    return stdout


def unbox_variant(output):
    text = safe_string(output)
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    if text.endswith(","):
        text = text[:-1].strip()
    while text.startswith("<") and text.endswith(">"):
        text = text[1:-1].strip()
    if text.startswith("'") and text.endswith("'"):
        return text[1:-1]
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if text.startswith("uint") or text.startswith("int"):
        parts = text.split(None, 1)
        if len(parts) == 2 and parts[1].isdigit():
            return int(parts[1])
    if text.isdigit():
        return int(text)
    return text


def introspect_xml(service, path):
    return run_gdbus("introspect", "--session", "--dest", service, "--object-path", path, "--xml")


def discover_roots(service):
    roots = []
    last_error = ""
    for path in ROOT_PATHS:
        try:
            xml_text = introspect_xml(service, path)
            if xml_text:
                roots.append(path)
        except Exception as exc:
            last_error = safe_string(exc)
    return roots, last_error


def find_device_paths(service, roots):
    paths = []
    seen = set()
    queue = list(roots)
    while queue:
        path = queue.pop(0)
        if path in seen:
            continue
        seen.add(path)
        try:
            xml_text = introspect_xml(service, path)
        except Exception:
            continue
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            continue
        if path.endswith(tuple(f"/event{{index}}" for index in range(0, 512))):
            paths.append(path)
        for node in root.findall("node"):
            name = safe_string(node.attrib.get("name"))
            if not name:
                continue
            child = path.rstrip("/") + "/" + name
            if re.fullmatch(r"event\\d+", name):
                paths.append(child)
            elif child not in seen and child not in queue and child.count("/") <= 6:
                queue.append(child)
    return sorted(set(paths))


def interface_properties(service, path):
    xml_text = introspect_xml(service, path)
    root = ET.fromstring(xml_text)
    props = {{}}
    for iface in root.findall("interface"):
        if iface.attrib.get("name") != DEVICE_INTERFACE:
            continue
        for prop in iface.findall("property"):
            name = safe_string(prop.attrib.get("name"))
            if name:
                props[name] = safe_string(prop.attrib.get("type"))
    return props


def get_property(service, path, name):
    value = run_gdbus(
        "call",
        "--session",
        "--dest",
        service,
        "--object-path",
        path,
        "--method",
        "org.freedesktop.DBus.Properties.Get",
        DEVICE_INTERFACE,
        name,
    )
    return unbox_variant(value)


def set_enabled(service, path, enabled, property_name="enabled"):
    variant = "<true>" if enabled else "<false>"
    run_gdbus(
        "call",
        "--session",
        "--dest",
        service,
        "--object-path",
        path,
        "--method",
        "org.freedesktop.DBus.Properties.Set",
        DEVICE_INTERFACE,
        property_name,
        variant,
    )


def normalize_hex(value):
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return f"{{value:04x}}"
    text = safe_string(value).lower()
    if not text:
        return ""
    if text.startswith("0x"):
        text = text[2:]
    if text.isdigit():
        return f"{{int(text):04x}}"
    return text


def normalize_name(value):
    text = safe_string(value).replace("_", " ").lower()
    return " ".join(part for part in text.split() if part)


def device_details(service, path):
    props = interface_properties(service, path)
    if not props:
        return {{}}
    values = {{}}
    for prop_name in props:
        try:
            values[prop_name] = get_property(service, path, prop_name)
        except Exception:
            continue
    name = ""
    for candidate in ["Name", "name", "SysName", "sysName", "sys_name"]:
        if candidate in values:
            name = safe_string(values[candidate])
            if name:
                break
    vendor = ""
    for candidate in ["Vendor", "vendor", "VendorId", "vendorId", "vendor_id"]:
        if candidate in values:
            vendor = normalize_hex(values[candidate])
            if vendor:
                break
    product = ""
    for candidate in ["Product", "product", "ProductId", "productId", "product_id"]:
        if candidate in values:
            product = normalize_hex(values[candidate])
            if product:
                break
    enabled = values.get("enabled")
    enabled_property = "enabled" if "enabled" in values else ""
    if not isinstance(enabled, bool):
        enabled = values.get("Enabled")
        if isinstance(enabled, bool):
            enabled_property = "Enabled"
    if not isinstance(enabled, bool):
        enabled = True
    if not enabled_property and "Enabled" in props:
        enabled_property = "Enabled"
    if not enabled_property and "enabled" in props:
        enabled_property = "enabled"
    supports_disable_events = values.get("supportsDisableEvents")
    if not isinstance(supports_disable_events, bool):
        supports_disable_events = values.get("SupportsDisableEvents")
    if not isinstance(supports_disable_events, bool):
        supports_disable_events = False
    return {{
        "name": name,
        "vendor": vendor,
        "product": product,
        "enabled": enabled,
        "enabled_property": enabled_property or "enabled",
        "supports_disable_events": supports_disable_events,
        "path": path,
    }}


def is_sunshine_device(info):
    name = normalize_name(info.get("name"))
    vendor = normalize_hex(info.get("vendor"))
    product = normalize_hex(info.get("product"))
    vendor_match = vendor == f"{{SUNSHINE_VENDOR_ID:04x}}"
    product_match = product == f"{{SUNSHINE_PRODUCT_ID:04x}}"
    name_match = any(normalize_name(marker) in name for marker in NAME_MARKERS)
    return name_match or (vendor_match and product_match)


def kwin_service():
    last_error = ""
    for service in SERVICE_CANDIDATES:
        roots, error = discover_roots(service)
        if roots:
            return service, roots, ""
        if error:
            last_error = error
    return "", [], last_error


def main():
    write_status(state="starting")
    if not is_plasma_session():
        write_status(state="inactive")
        return 0

    service, roots, discovery_error = kwin_service()
    if not service:
        write_status(state="failed", last_error=discovery_error or "KWin InputDevice DBus service not found.")
        return 0

    disabled = {{}}
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGHUP, handle_signal)

    try:
        while not STOP:
            seen_count = 0
            current_paths = set()
            failed_devices = []
            try:
                for path in find_device_paths(service, roots):
                    try:
                        info = device_details(service, path)
                    except Exception:
                        continue
                    if not is_sunshine_device(info):
                        continue
                    seen_count += 1
                    current_paths.add(path)
                    if not info.get("enabled", True):
                        if path not in disabled:
                            disabled[path] = {{
                                "path": path,
                                "name": safe_string(info.get("name")),
                                "enabled_before": False,
                                "enabled_property": safe_string(info.get("enabled_property")) or "enabled",
                            }}
                        continue
                    if not info.get("supports_disable_events", False):
                        failed_devices.append({{
                            "path": path,
                            "name": safe_string(info.get("name")),
                            "error": "KWin reports supportsDisableEvents=false",
                        }})
                        continue
                    if info.get("enabled", True):
                        try:
                            set_enabled(
                                service,
                                path,
                                False,
                                safe_string(info.get("enabled_property")) or "enabled",
                            )
                            disabled[path] = {{
                                "path": path,
                                "name": safe_string(info.get("name")),
                                "enabled_before": True,
                                "enabled_property": safe_string(info.get("enabled_property")) or "enabled",
                            }}
                        except Exception as exc:
                            failed_devices.append({{
                                "path": path,
                                "name": safe_string(info.get("name")),
                                "error": safe_string(exc),
                            }})
                disabled_devices = []
                for path, details in list(disabled.items()):
                    if path not in current_paths:
                        continue
                    disabled_devices.append({{"path": path, "name": details["name"]}})
                write_status(
                    state="active",
                    service=service,
                    disabled_devices=disabled_devices,
                    failed_devices=failed_devices,
                    seen_device_count=seen_count,
                )
            except Exception as exc:
                write_status(
                    state="failed",
                    service=service,
                    disabled_devices=[{{"path": path, "name": details["name"]}} for path, details in disabled.items()],
                    failed_devices=failed_devices,
                    seen_device_count=0,
                    last_error=safe_string(exc),
                )
            time.sleep(1.0)
    finally:
        restore_error = ""
        for path, details in list(disabled.items()):
            try:
                set_enabled(
                    service,
                    path,
                    bool(details.get("enabled_before", True)),
                    safe_string(details.get("enabled_property")) or "enabled",
                )
            except Exception as exc:
                restore_error = safe_string(exc)
        write_status(
            state="restored" if not restore_error else "failed",
            service=service,
            disabled_devices=[],
            failed_devices=[],
            seen_device_count=0,
            last_error=restore_error,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""


def _script_templates(state: Dict[str, Any]) -> Dict[Path, str]:
    paths = state["paths"]
    sunshine_command = _safe_string(state.get("sunshine_execstart")) or _sunshine_binary() or "sunshine"
    sunshine_unit = _sunshine_unit()
    audio_sink = state["audio_sink"]
    refresh_rate_sync_mode = _normalized_refresh_rate_sync_mode(state.get("refresh_rate_sync_mode"))
    custom_mode = _normalized_custom_display_mode(state.get("custom_display_mode"))
    custom_width = custom_mode["width"]
    custom_height = custom_mode["height"]
    custom_refresh = _format_refresh_rate_hz(custom_mode["refresh"]) or str(FALLBACK_FPS)
    managed_audio_sinks = _managed_audio_sink_names(state)
    managed_audio_sources = _managed_audio_source_names(state)
    mangohud_fps_limit_block = ""
    mangohud_env_append_block = ""
    if state.get("dynamic_mangohud_fps_limit"):
        mangohud_fps_limit_block = """
    mangohud_config_value=""
    local resolved_stream_fps
    resolved_stream_fps="$("{resolve_stream_fps_script}" "{refresh_rate_sync_mode}" fallback)"
    if [ -n "$resolved_stream_fps" ]; then
        mangohud_config_value="read_cfg,fps_limit=$resolved_stream_fps"
    fi
""".replace("{resolve_stream_fps_script}", paths["resolve_stream_fps_script"]).replace("{refresh_rate_sync_mode}", refresh_rate_sync_mode)
        mangohud_env_append_block = """
    if [ -n "$mangohud_config_value" ]; then
        launch_command+=("MANGOHUD_CONFIG=$mangohud_config_value")
    fi
"""
    return {
        Path(paths["sway_config"]): f"""# Managed by LutrisToSunshine display.
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
dbus_value="${{DBUS_SESSION_BUS_ADDRESS:-unix:path=$runtime_dir/bus}}"
wayland_value="$(cat "$display_file")"

unset DISPLAY
export XDG_RUNTIME_DIR="$runtime_dir"
export DBUS_SESSION_BUS_ADDRESS="$dbus_value"
export WAYLAND_DISPLAY="$wayland_value"
export SWAYSOCK="{state['sway_socket']}"
export XDG_SESSION_TYPE=wayland
export XDG_CURRENT_DESKTOP=sway
export XDG_SESSION_DESKTOP=sway

exec /bin/sh -lc {shlex.quote(sunshine_command)}
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
kwin_input_isolation_script="{paths['kwin_input_isolation_script']}"
sway_start_script="{paths['sway_start_script']}"
sunshine_start_script="{paths['sunshine_start_script']}"
display_file="{paths['wayland_display_file']}"
bridge_status_file="{paths['input_bridge_status_file']}"
kwin_input_isolation_status_file="{paths['kwin_input_isolation_status_file']}"
sway_socket="{state['sway_socket']}"
runtime_dir="${{XDG_RUNTIME_DIR:-/run/user/$(id -u)}}"
dbus_value="${{DBUS_SESSION_BUS_ADDRESS:-unix:path=$runtime_dir/bus}}"
pulse_server_value="${{PULSE_SERVER:-}}"
pulse_clientconfig_value="${{PULSE_CLIENTCONFIG:-}}"
audio_guard_pid=""
input_bridge_pid=""
kwin_input_isolation_pid=""
sway_pid=""
sunshine_pid=""
sunshine_status=0

export XDG_RUNTIME_DIR="$runtime_dir"
export DBUS_SESSION_BUS_ADDRESS="$dbus_value"
if [ -n "$pulse_server_value" ]; then
    export PULSE_SERVER="$pulse_server_value"
else
    unset PULSE_SERVER
fi
if [ -n "$pulse_clientconfig_value" ]; then
    export PULSE_CLIENTCONFIG="$pulse_clientconfig_value"
else
    unset PULSE_CLIENTCONFIG
fi

prepare_audio_state() {{
    python3 - "$state_path" "$sunshine_conf" "$audio_sink" <<'PY'
import json
import os
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


def pactl_env():
    env = dict(os.environ)
    env["LANG"] = "C"
    env["LC_ALL"] = "C"
    return env

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
        env=pactl_env(),
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
        if not (current_sink in managed_sinks or current_source in managed_sources):
            state["host_audio_defaults"] = {{"sink": current_sink, "source": current_source}}

state_path.parent.mkdir(parents=True, exist_ok=True)
state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
PY
}}

restore_audio_state() {{
    python3 - "$state_path" "$sunshine_conf" <<'PY'
import json
import os
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


def pactl_env():
    env = dict(os.environ)
    env["LANG"] = "C"
    env["LC_ALL"] = "C"
    return env

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
    subprocess.run(["pactl", "set-default-sink", sink_name], text=True, capture_output=True, check=False, env=pactl_env())
if source_name:
    subprocess.run(["pactl", "set-default-source", source_name], text=True, capture_output=True, check=False, env=pactl_env())
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
    stop_child "$kwin_input_isolation_pid"
    stop_child "$audio_guard_pid"
    stop_child "$sway_pid"
    rm -f "$bridge_status_file" "$kwin_input_isolation_status_file" "$display_file"
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

setsid python3 "$kwin_input_isolation_script" &
kwin_input_isolation_pid=$!

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
runtime_dir="${{XDG_RUNTIME_DIR:-/run/user/$(id -u)}}"
dbus_value="${{DBUS_SESSION_BUS_ADDRESS:-unix:path=$runtime_dir/bus}}"
pulse_server_value="${{PULSE_SERVER:-}}"
pulse_clientconfig_value="${{PULSE_CLIENTCONFIG:-}}"

run_audio_command() {{
    local command=(/usr/bin/env
        "XDG_RUNTIME_DIR=$runtime_dir"
        "DBUS_SESSION_BUS_ADDRESS=$dbus_value"
        "LANG=C"
        "LC_ALL=C"
    )
    if [ -n "$pulse_server_value" ]; then
        command+=("PULSE_SERVER=$pulse_server_value")
    fi
    if [ -n "$pulse_clientconfig_value" ]; then
        command+=("PULSE_CLIENTCONFIG=$pulse_clientconfig_value")
    fi
    "${{command[@]}}" "$@"
}}

if run_audio_command pactl list short sinks | awk '{{print $2}}' | grep -Fx "$sink_name" >/dev/null 2>&1; then
    rm -f "$module_file"
    exit 0
fi

module_id="$(run_audio_command pactl load-module module-null-sink sink_name="$sink_name" sink_properties=device.description='LutrisToSunshine Virtual Display')"
printf '%s\\n' "$module_id" > "$module_file"
""",
        Path(paths["audio_cleanup_script"]): f"""#!/bin/bash
set -euo pipefail

module_file="{paths['audio_module_file']}"
runtime_dir="${{XDG_RUNTIME_DIR:-/run/user/$(id -u)}}"
dbus_value="${{DBUS_SESSION_BUS_ADDRESS:-unix:path=$runtime_dir/bus}}"
pulse_server_value="${{PULSE_SERVER:-}}"
pulse_clientconfig_value="${{PULSE_CLIENTCONFIG:-}}"

run_audio_command() {{
    local command=(/usr/bin/env
        "XDG_RUNTIME_DIR=$runtime_dir"
        "DBUS_SESSION_BUS_ADDRESS=$dbus_value"
        "LANG=C"
        "LC_ALL=C"
    )
    if [ -n "$pulse_server_value" ]; then
        command+=("PULSE_SERVER=$pulse_server_value")
    fi
    if [ -n "$pulse_clientconfig_value" ]; then
        command+=("PULSE_CLIENTCONFIG=$pulse_clientconfig_value")
    fi
    "${{command[@]}}" "$@"
}}

if [ ! -f "$module_file" ]; then
    exit 0
fi

module_id="$(cat "$module_file")"
if [ -n "$module_id" ]; then
    run_audio_command pactl unload-module "$module_id" >/dev/null 2>&1 || true
fi
rm -f "$module_file"
""",
        Path(paths["audio_guard_script"]): f"""#!/bin/bash
set -euo pipefail

state_path="{paths['state_path']}"
poll_interval="{AUDIO_GUARD_POLL_INTERVAL_SECONDS}"
runtime_dir="${{XDG_RUNTIME_DIR:-/run/user/$(id -u)}}"
dbus_value="${{DBUS_SESSION_BUS_ADDRESS:-unix:path=$runtime_dir/bus}}"
pulse_server_value="${{PULSE_SERVER:-}}"
pulse_clientconfig_value="${{PULSE_CLIENTCONFIG:-}}"

run_audio_command() {{
    local command=(/usr/bin/env
        "XDG_RUNTIME_DIR=$runtime_dir"
        "DBUS_SESSION_BUS_ADDRESS=$dbus_value"
        "LANG=C"
        "LC_ALL=C"
    )
    if [ -n "$pulse_server_value" ]; then
        command+=("PULSE_SERVER=$pulse_server_value")
    fi
    if [ -n "$pulse_clientconfig_value" ]; then
        command+=("PULSE_CLIENTCONFIG=$pulse_clientconfig_value")
    fi
    "${{command[@]}}" "$@"
}}

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

enforce_host_defaults() {{
    if ! pactl_info="$(run_audio_command pactl info 2>/dev/null)"; then
        return 0
    fi

    current_sink="$(printf '%s\\n' "$pactl_info" | awk -F': ' '/^Default Sink:/ {{print $2; exit}}')"
    current_source="$(printf '%s\\n' "$pactl_info" | awk -F': ' '/^Default Source:/ {{print $2; exit}}')"

    mapfile -t host_defaults < <(read_host_defaults)
    host_sink="${{host_defaults[0]:-}}"
    host_source="${{host_defaults[1]:-}}"

    if [ -n "$host_sink" ] && [ "$current_sink" != "$host_sink" ] && is_managed_sink "$current_sink"; then
        run_audio_command pactl set-default-sink "$host_sink" >/dev/null 2>&1 || true
    fi
    if [ -n "$host_source" ] && [ "$current_source" != "$host_source" ] && is_managed_source "$current_source"; then
        run_audio_command pactl set-default-source "$host_source" >/dev/null 2>&1 || true
    fi
}}

poll_host_defaults() {{
    while true; do
        enforce_host_defaults
        sleep "$poll_interval"
    done
}}

poll_host_defaults
""",
        Path(paths["input_bridge_script"]): _input_bridge_script(state),
        Path(paths["kwin_input_isolation_script"]): _kwin_input_isolation_script(state),
        Path(paths["headless_prep_script"]): f"""#!/bin/bash
set -euo pipefail

encoded_command="${{1:-}}"

if [ -z "$encoded_command" ]; then
    echo "Missing encoded prep command." >&2
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
launch_log_file="{paths['last_launch_log_file']}"
portal_timeout="{FLATPAK_PORTAL_SWITCH_TIMEOUT}"
spawn_timeout="{FLATPAK_PORTAL_SPAWN_TIMEOUT}"

mkdir -p "$(dirname "$launch_log_file")"
touch "$launch_log_file"
chmod 600 "$launch_log_file" >/dev/null 2>&1 || true

log_debug() {{
    printf '[%s] prep %s\\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$launch_log_file"
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

run_headless_command() {{
    local command_to_run="$1"
    log_debug "running prep command: $command_to_run"
    local -a launch_command
    launch_command=(/usr/bin/env -i
        "HOME=$home_value"
        "USER=$user_value"
        "LOGNAME=$logname_value"
        "SHELL=$shell_value"
        "PATH=$path_value"
        "LANG=$lang_value"
        "XDG_RUNTIME_DIR=$runtime_dir"
        "DBUS_SESSION_BUS_ADDRESS=$dbus_value"
        "DISPLAY=$display_value"
        "WAYLAND_DISPLAY=$wayland_value"
        "SWAYSOCK={state['sway_socket']}"
        "XDG_SESSION_TYPE=wayland"
        "XDG_CURRENT_DESKTOP=sway"
        "XDG_SESSION_DESKTOP=sway"
        "DESKTOP_SESSION=sway"
        "LTS_VDISPLAY=1"
        "PULSE_SINK={audio_sink}"
        "PULSE_SERVER=$pulse_server_value"
        "PULSE_CLIENTCONFIG=$pulse_clientconfig_value"
    )
    launch_command+=(/bin/sh -lc "$command_to_run")
    "${{launch_command[@]}}" >>"$launch_log_file" 2>&1
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
portal_switched=0

cleanup() {{
    local exit_code=$?
    if [ "${{portal_switched:-0}}" -eq 1 ]; then
        restore_host_portal_env >/dev/null 2>&1 || true
    fi
    cleanup_monitor
    rm -f "$host_env_file" "$systemd_env_dump"
    exit "$exit_code"
}}

trap cleanup EXIT INT TERM HUP

if is_flatpak_command "$decoded_command"; then
    exec 9>"$portal_lock_file"
    if ! flock -w "$portal_timeout" 9; then
        echo "Another Flatpak virtual-display launch is already switching the portal environment." >&2
        exit 1
    fi

    host_env_file="$(mktemp "{PROFILE_ROOT}/portal-env-XXXXXX")"
    systemd_env_dump="$(mktemp "{PROFILE_ROOT}/systemd-env-XXXXXX")"
    log_debug "starting transient Flatpak portal handoff for prep command"

    snapshot_host_env
    start_portal_monitor
    apply_headless_portal_env
    portal_switched=1
fi

run_headless_command "$decoded_command"

if [ "${{portal_switched:-0}}" -eq 1 ]; then
    if ! wait_for_spawn; then
        log_debug "portal handoff timed out waiting for SpawnStarted on prep command"
    else
        log_debug "portal handoff SpawnStarted signal observed for prep command"
    fi
fi
""",
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
mangohud_config_value=""

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
{mangohud_fps_limit_block.rstrip()}
    log_debug "launch command: $command_to_run"
    local -a launch_command
    launch_command=(/usr/bin/env -i
        "HOME=$home_value"
        "USER=$user_value"
        "LOGNAME=$logname_value"
        "SHELL=$shell_value"
        "PATH=$path_value"
        "LANG=$lang_value"
        "XDG_RUNTIME_DIR=$runtime_dir"
        "DBUS_SESSION_BUS_ADDRESS=$dbus_value"
        "DISPLAY=$display_value"
        "WAYLAND_DISPLAY=$wayland_value"
        "SWAYSOCK={state['sway_socket']}"
        "XDG_SESSION_TYPE=wayland"
        "XDG_CURRENT_DESKTOP=sway"
        "XDG_SESSION_DESKTOP=sway"
        "DESKTOP_SESSION=sway"
        "LTS_LAUNCH_ID=$launch_id"
        "LTS_VDISPLAY=1"
        "PULSE_SINK={audio_sink}"
        "PULSE_SERVER=$pulse_server_value"
        "PULSE_CLIENTCONFIG=$pulse_clientconfig_value"
    )
{mangohud_env_append_block.rstrip()}
    launch_command+=(/bin/sh -lc "$command_to_run")
    setsid "${{launch_command[@]}}" >>"$launch_log_file" 2>&1 &
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
    local mangohud_config_value_field="${{mangohud_config_value:-}}"
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
mangohud_config=$mangohud_config_value_field
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
        Path(paths["resolve_stream_fps_script"]): f"""#!/bin/bash
set -euo pipefail

requested_fps="${{SUNSHINE_CLIENT_FPS:-}}"
mode_override="${{1:-}}"
fallback_mode="${{2:-fallback}}"
since_time="${{3:-}}"
mode="{refresh_rate_sync_mode}"
custom_fps="{custom_refresh}"

if [ -n "$mode_override" ]; then
    mode="$mode_override"
fi

if [ "$mode" = "custom" ]; then
    printf '%s\\n' "$custom_fps"
    exit 0
fi

if [ -z "$requested_fps" ]; then
    exit 0
fi

if [ "$mode" != "exact" ]; then
    printf '%s\\n' "$requested_fps"
    exit 0
fi

if ! command -v journalctl >/dev/null 2>&1; then
    printf '%s\\n' "$requested_fps"
    exit 0
fi

attempts=100
while [ "$attempts" -gt 0 ]; do
    journal_output_file="$(mktemp)"
    journalctl --user -u "{sunshine_unit}" -n 200 --no-pager -o cat >"$journal_output_file" 2>/dev/null || true
    resolved_fps="$(python3 - "$requested_fps" "$journal_output_file" "$since_time" <<'PY'
from datetime import datetime
import re
import sys

journal_output_path = sys.argv[2]
since_time = sys.argv[3].strip()
since_epoch = None
if since_time:
    try:
        since_epoch = float(since_time)
    except ValueError:
        since_epoch = None

timestamp_pattern = re.compile(r"^\\[(\\d{{4}}-\\d{{2}}-\\d{{2}} \\d{{2}}:\\d{{2}}:\\d{{2}}(?:\\.\\d+)?)\\]:")

def parse_epoch(line: str):
    match = timestamp_pattern.match(line)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S.%f").timestamp()
    except ValueError:
        try:
            return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            return None

with open(journal_output_path, "r", encoding="utf-8", errors="replace") as handle:
    raw_lines = [line.strip() for line in handle.read().splitlines() if line.strip()]

lines = []
for line in raw_lines:
    if since_epoch is None:
        lines.append(line)
        continue
    line_epoch = parse_epoch(line)
    if line_epoch is not None and line_epoch >= since_epoch:
        lines.append(line)

connect_index = -1
for index, line in enumerate(lines):
    if "CLIENT CONNECTED" in line:
        connect_index = index

pattern = re.compile(r"Requested frame rate \\[(\\d+)/(\\d+)(?:, approx\\. [^\\]]+)?\\]")
search_lines = lines[connect_index + 1 :] if connect_index >= 0 else lines
for line in reversed(search_lines):
    match = pattern.search(line)
    if not match:
        continue
    numerator = int(match.group(1))
    denominator = int(match.group(2))
    if denominator <= 0:
        break
    fps = numerator / denominator
    nearest = round(fps)
    if abs(fps - nearest) < 0.005:
        print(str(int(nearest)))
    else:
        print(f"{{fps:.2f}}")
    raise SystemExit(0)

print("")
PY
)"
    rm -f "$journal_output_file"
    if [ -n "$resolved_fps" ]; then
        printf '%s\\n' "$resolved_fps"
        exit 0
    fi
    attempts=$((attempts - 1))
    sleep 0.1
done

if [ "$fallback_mode" = "none" ]; then
    exit 0
fi

printf '%s\\n' "$requested_fps"
""".replace("{custom_refresh}", custom_refresh),
        Path(paths["apply_exact_refresh_script"]): f"""#!/bin/bash
set -euo pipefail

if [ "${{#}}" -lt 2 ]; then
    exit 0
fi

width="${{1}}"
height="${{2}}"
since_time="${{3:-}}"

exact_stream_fps="$("{paths['resolve_stream_fps_script']}" exact none "$since_time")"
if [ -z "$exact_stream_fps" ]; then
    exit 0
fi

SWAYSOCK="{state['sway_socket']}" swaymsg "output HEADLESS-1 mode ${{width}}x${{height}}@${{exact_stream_fps}}Hz" >/dev/null 2>&1 || true
""",
        Path(paths["set_resolution_script"]): f"""#!/bin/bash
set -euo pipefail

if [ ! -S "{state['sway_socket']}" ]; then
    exit 0
fi

mode="{refresh_rate_sync_mode}"
target_width="${{SUNSHINE_CLIENT_WIDTH:-}}"
target_height="${{SUNSHINE_CLIENT_HEIGHT:-}}"
target_fps="${{SUNSHINE_CLIENT_FPS:-}}"

if [ "$mode" = "custom" ]; then
    target_width="{custom_width}"
    target_height="{custom_height}"
    target_fps="{custom_refresh}"
elif [ -z "$target_width" ] || [ -z "$target_height" ] || [ -z "$target_fps" ]; then
    exit 0
fi

sync_since="$(python3 - <<'PY'
import time
print(f"{{time.time():.6f}}")
PY
)"
SWAYSOCK="{state['sway_socket']}" swaymsg "output HEADLESS-1 mode ${{target_width}}x${{target_height}}@${{target_fps}}Hz" >/dev/null 2>&1 || true
if [ "$mode" = "exact" ]; then
    setsid "{paths['apply_exact_refresh_script']}" "${{target_width}}" "${{target_height}}" "$sync_since" >/dev/null 2>&1 &
fi
# Removed sleep after resolution change for faster startup
""".replace("{custom_width}", str(custom_width)).replace("{custom_height}", str(custom_height)).replace("{custom_refresh}", custom_refresh),
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


def _udev_rule(isolation_mode: Optional[str] = None) -> str:
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
    if _sunshine_binary() is None and not _current_sunshine_execstart(_sunshine_unit()):
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
    if not _safe_string(state.get("sunshine_execstart")):
        state = _remember_sunshine_execstart(state)
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


def _sunshine_unit_exists(unit: str) -> bool:
    result = _systemctl_user("show", "--property=LoadState", "--value", unit)
    return result.returncode == 0 and _safe_string(result.stdout) not in {"", "not-found"}


def _systemd_unit_id(unit: str) -> str:
    result = _systemctl_user("show", "--property=Id", "--value", unit)
    if result.returncode != 0:
        return ""
    return _safe_string(result.stdout)


def _sunshine_unit() -> str:
    candidates = (
        SUNSHINE_UNIT,
        FALLBACK_SUNSHINE_UNIT,
    )
    for unit in candidates:
        if _sunshine_unit_exists(unit) and _systemctl_user("is-active", unit).returncode == 0:
            return _systemd_unit_id(unit) or unit
    for unit in candidates:
        if _sunshine_unit_exists(unit):
            return _systemd_unit_id(unit) or unit
    return SUNSHINE_UNIT


def _managed_sunshine_units(state: Optional[Dict[str, Any]] = None) -> List[str]:
    units = [SUNSHINE_UNIT, FALLBACK_SUNSHINE_UNIT]
    if state is not None:
        saved_unit = _safe_string(state.get("sunshine_unit_name"))
        if saved_unit and saved_unit not in units:
            units.insert(0, saved_unit)
    return units


def _managed_override_paths(state: Dict[str, Any]) -> List[Path]:
    systemd_user_dir = Path(state["paths"]["systemd_user_dir"])
    paths: List[Path] = []
    for unit in _managed_sunshine_units(state):
        override_dir = systemd_user_dir / f"{unit}.d"
        paths.append(override_dir / "override.conf")
        paths.append(override_dir)
    return paths


def _managed_setup_paths(state: Dict[str, Any]) -> List[Path]:
    paths = state["paths"]
    keys = [
        "sway_config",
        "sway_start_script",
        "sunshine_start_script",
        "sunshine_wrapper_script",
        "audio_create_script",
        "audio_cleanup_script",
        "audio_guard_script",
        "launch_app_script",
        "resolve_stream_fps_script",
        "apply_exact_refresh_script",
        "headless_prep_script",
        "set_resolution_script",
        "reset_resolution_script",
        "input_bridge_script",
        "kwin_input_isolation_script",
    ]
    return [Path(paths[key]) for key in keys if _safe_string(paths.get(key))]


def _sunshine_service_active() -> bool:
    return _systemctl_user("is-active", _sunshine_unit()).returncode == 0


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
    _systemctl_user("restart", _sunshine_unit())


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


def setup_display() -> int:
    missing = _ensure_dependencies()
    if missing:
        print("Missing required commands:", ", ".join(missing))
        return 1

    state = load_state()
    sunshine_was_active = _sunshine_service_active()
    state = _remember_sunshine_execstart(state)
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

    _daemon_reload()
    state["enabled"] = True
    save_state(state)
    if sunshine_was_active:
        _systemctl_user("restart", _sunshine_unit())

    print("Virtual display files installed.")
    return 0


def start_display() -> int:
    state = load_state()
    if not state.get("enabled"):
        print("Virtual display is not set up. Run 'python3 lutristosunshine.py display enable' first.")
        return 1
    state = refresh_managed_files(state)
    _remember_sunshine_audio_sink(state)
    _snapshot_host_audio_defaults(state)
    save_state(state)
    sunshine_unit = _sunshine_unit()
    result = _systemctl_user("start", sunshine_unit)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        _systemctl_user("stop", sunshine_unit)
        _restore_sunshine_audio_sink(state)
        _restore_host_audio_defaults(state)
        save_state(state)
        if stderr:
            print(stderr)
        print("Error: unable to start the virtual display Sunshine service.")
        return 1
    print("Virtual display started.")
    return 0


def restart_display() -> int:
    state = load_state()
    if not state.get("enabled"):
        print("Virtual display is not set up. Run 'python3 lutristosunshine.py display enable' first.")
        return 1
    stop_display()
    return start_display()


def stop_display() -> int:
    state = load_state()
    if not state.get("enabled"):
        print("Virtual display is not set up.")
        return 1
    _systemctl_user("stop", _sunshine_unit())
    _clear_input_bridge_status_file(state)
    try:
        Path(state["paths"]["kwin_input_isolation_status_file"]).unlink()
    except OSError:
        pass
    _restore_sunshine_audio_sink(state)
    _restore_host_audio_defaults(state)
    save_state(state)
    print("Virtual display stopped.")
    return 0


def display_snapshot() -> Dict[str, Any]:
    state = load_state()
    configured = bool(state.get("enabled"))
    custom_mode = _normalized_custom_display_mode(state.get("custom_display_mode"))
    active_launch_status = _active_launch_status(state)
    sunshine_active = _sunshine_service_active() if configured else False
    host_session = _host_session_name()
    input_isolation_mode = _input_isolation_mode()
    host_defaults = state.get("host_audio_defaults") or {}
    wayland_display = ""
    if WAYLAND_DISPLAY_PATH.exists():
        wayland_display = WAYLAND_DISPLAY_PATH.read_text(encoding="utf-8").strip()
    sway_active = bool(
        configured
        and sunshine_active
        and Path(state["sway_socket"]).exists()
        and WAYLAND_DISPLAY_PATH.exists()
    )
    current_headless_mode = _current_headless_mode(state, sunshine_active, sway_active)
    portal_handoff_active = PORTAL_ACTIVE_PATH.exists()
    audio_guard_state = "active" if sunshine_active and Path(state["paths"]["audio_module_file"]).exists() else "inactive"
    kwin_status = _kwin_input_isolation_status(state) if configured else _empty_kwin_input_isolation_status()
    sunshine_input_devices = _sunshine_virtual_input_devices() if configured else []
    selections = state["exclusive_input_devices"]["devices"]
    devices, device_error = _list_controller_devices() if configured and selections else ([], None)
    runtime_status = _input_bridge_status(state) if configured else {}
    runtime_by_id = {}
    for item in runtime_status.get("devices", []):
        if isinstance(item, dict) and item.get("selection_id"):
            runtime_by_id[item["selection_id"]] = item

    controller_rows: List[Dict[str, str]] = []
    for selection in selections:
        runtime = runtime_by_id.get(selection["selection_id"], {})
        current_match = next(
            (device for device in devices if _selection_matches_device(selection, device)),
            None,
        )
        if runtime:
            message = _safe_string(runtime.get("message"))
            identity_bits = _selection_runtime_identity_bits(runtime)
            detail_parts = []
            if identity_bits:
                detail_parts.append(" | ".join(identity_bits))
            if message:
                detail_parts.append(message)
            controller_rows.append(
                {
                    "label": selection["label"],
                    "state": _safe_string(runtime.get("state")) or "unknown",
                    "details": " - ".join(detail_parts),
                }
            )
        elif current_match:
            controller_rows.append(
                {
                    "label": selection["label"],
                    "state": "detected",
                    "details": current_match["event_path"],
                }
            )
        else:
            controller_rows.append(
                {
                    "label": selection["label"],
                    "state": "missing",
                    "details": "",
                }
            )

    snapshot = {
        "configured": configured,
        "dynamic_mangohud_fps_limit": bool(state.get("dynamic_mangohud_fps_limit")),
        "refresh_rate_sync_mode": _normalized_refresh_rate_sync_mode(state.get("refresh_rate_sync_mode")),
        "custom_display_mode": custom_mode,
        "custom_display_mode_summary": _custom_display_mode_string(custom_mode),
        "profile": state["profile"],
        "host_session": host_session,
        "input_isolation_mode": input_isolation_mode,
        "sunshine_unit": _sunshine_unit(),
        "sunshine_active": sunshine_active,
        "sway_active": sway_active,
        "bridge_state": _bridge_service_state() if configured else "inactive",
        "audio_guard_state": audio_guard_state,
        "audio_sink": state["audio_sink"],
        "host_audio_defaults": {
            "sink": _safe_string(host_defaults.get("sink")),
            "source": _safe_string(host_defaults.get("source")),
        },
        "wayland_display": wayland_display,
        "current_headless_mode": current_headless_mode,
        "sway_socket": state["sway_socket"],
        "udev_rule_path": state["udev_rule_path"],
        "udev_rule_present": Path(state["udev_rule_path"]).exists(),
        "kwin_isolation_state": kwin_status["state"],
        "kwin_isolation_service": kwin_status["service"],
        "kwin_isolation_error": kwin_status["last_error"],
        "kwin_isolation_seen_device_count": kwin_status["seen_device_count"],
        "kwin_isolation_devices": kwin_status["disabled_devices"],
        "kwin_isolation_failed_devices": kwin_status["failed_devices"],
        "sunshine_input_devices": sunshine_input_devices,
        "sunshine_input_device_count": len(sunshine_input_devices),
        "portal_handoff_active": portal_handoff_active,
        "current_mangohud_config": _safe_string(active_launch_status.get("mangohud_config")),
        "last_launch_log_file": state["paths"]["last_launch_log_file"],
        "controllers": controller_rows,
        "controller_count": len(selections),
        "controller_detection_error": device_error,
        "dependencies_missing": _ensure_dependencies(),
        "next_step": "",
    }
    if not configured:
        snapshot["next_step"] = "Run 'python3 lutristosunshine.py display enable' to set up the headless stack."
    elif not sunshine_active:
        snapshot["next_step"] = "Run 'python3 lutristosunshine.py display start' to start the managed stack."
    elif not sway_active:
        snapshot["next_step"] = "Run 'python3 lutristosunshine.py display status' or '... display logs' to inspect why headless Sway is not ready."
    elif selections and snapshot["bridge_state"] != "active":
        snapshot["next_step"] = "Run 'python3 lutristosunshine.py display status' or '... display logs' if selected controllers are not being bridged."
    else:
        snapshot["next_step"] = "Virtual display is ready. Use 'controllers', 'rumble', or 'logs' for follow-up actions."
    return snapshot


def display_doctor_report() -> Dict[str, Any]:
    snapshot = display_snapshot()
    checks = []
    missing = snapshot["dependencies_missing"]
    checks.append(
        {
            "label": "Host session",
            "status": "pass" if snapshot["host_session"] != "unknown" else "info",
            "message": (
                f"{snapshot['host_session']} detected; using {snapshot['input_isolation_mode']}."
                if snapshot["host_session"] != "unknown"
                else f"Host session not detected; using {snapshot['input_isolation_mode']}."
            ),
        }
    )
    if missing:
        checks.append(
            {
                "label": "Dependencies",
                "status": "fail",
                "message": f"Missing required commands: {', '.join(missing)}",
            }
        )
    else:
        checks.append(
            {
                "label": "Dependencies",
                "status": "pass",
                "message": "Required commands are available.",
            }
        )

    if not snapshot["configured"]:
        checks.append(
            {
                "label": "Setup",
                "status": "fail",
                "message": "Virtual display is not configured.",
            }
        )
    else:
        checks.append(
            {
                "label": "Setup",
                "status": "pass",
                "message": "Managed files are installed.",
            }
        )
        checks.append(
            {
                "label": "Managed udev rule",
                "status": (
                    "pass" if snapshot["udev_rule_present"] else "warn"
                ),
                "message": (
                    "Input permissions rule is present." if snapshot["udev_rule_present"] else "Managed udev rule is missing."
                ),
            }
        )
        if snapshot["input_isolation_mode"] == "kwin-runtime-disable":
            kwin_active_but_not_isolated = (
                snapshot["kwin_isolation_state"] == "active"
                and snapshot["sunshine_input_device_count"] > 0
                and not snapshot["kwin_isolation_devices"]
            )
            checks.append(
                {
                    "label": "KWin isolation",
                    "status": (
                        "warn"
                        if snapshot["kwin_isolation_state"] in {"inactive", "failed"} or kwin_active_but_not_isolated
                        else "pass"
                    ),
                    "message": (
                        snapshot["kwin_isolation_error"]
                        if snapshot["kwin_isolation_state"] == "failed" and snapshot["kwin_isolation_error"]
                        else (
                            "KWin helper is not running."
                            if snapshot["kwin_isolation_state"] == "inactive"
                            else (
                                "KWin helper is starting."
                                if snapshot["kwin_isolation_state"] == "starting"
                                else (
                                    (
                                        f"KWin helper active via {snapshot['kwin_isolation_service'] or 'gdbus'}; "
                                        f"disabled {len(snapshot['kwin_isolation_devices'])} of "
                                        f"{snapshot['sunshine_input_device_count']} Sunshine input device(s)."
                                    )
                                    if snapshot["kwin_isolation_state"] == "active" and snapshot["kwin_isolation_devices"]
                                    else (
                                        (
                                            f"KWin helper active via {snapshot['kwin_isolation_service'] or 'gdbus'}; "
                                            f"matched {snapshot['kwin_isolation_seen_device_count']} of "
                                            f"{snapshot['sunshine_input_device_count']} host Sunshine input device(s), "
                                            "but disabled 0."
                                        )
                                        if kwin_active_but_not_isolated
                                        else "KWin helper is waiting for Sunshine virtual input devices to appear."
                                    )
                                )
                            )
                        )
                    ),
                }
            )
        checks.append(
            {
                "label": "Sunshine service",
                "status": "pass" if snapshot["sunshine_active"] else "warn",
                "message": "Managed Sunshine unit is active." if snapshot["sunshine_active"] else "Managed Sunshine unit is not active.",
            }
        )
        checks.append(
            {
                "label": "Headless display",
                "status": "pass" if snapshot["sway_active"] else "warn",
                "message": (
                    f"Headless Wayland display is ready ({snapshot['wayland_display']})."
                    if snapshot["sway_active"]
                    else "Headless Wayland display is not ready."
                ),
            }
        )
        checks.append(
            {
                "label": "Controller bridge",
                "status": "pass" if snapshot["bridge_state"] == "active" else "info",
                "message": (
                    f"{snapshot['controller_count']} host controller(s) configured; bridge is active."
                    if snapshot["bridge_state"] == "active"
                    else (
                        "No host controllers configured."
                        if snapshot["controller_count"] == 0
                        else f"{snapshot['controller_count']} host controller(s) configured; bridge state is {snapshot['bridge_state']}."
                    )
                ),
            }
        )
    if snapshot["controller_detection_error"]:
        checks.append(
            {
                "label": "Controller detection",
                "status": "warn",
                "message": snapshot["controller_detection_error"],
            }
        )

    summary = "healthy"
    if any(item["status"] == "fail" for item in checks):
        summary = "needs_attention"
    elif any(item["status"] == "warn" for item in checks):
        summary = "degraded"

    return {
        "summary": summary,
        "checks": checks,
        "next_step": snapshot["next_step"],
    }


def display_status() -> int:
    snapshot = display_snapshot()
    if not snapshot["configured"]:
        print("Virtual display is not configured.")
        return 1

    print("Virtual display status")
    print(f"Profile: {snapshot['profile']}")
    print(f"Host session: {snapshot['host_session']}")
    isolation_state = snapshot["kwin_isolation_state"] if snapshot["input_isolation_mode"] == "kwin-runtime-disable" else "ready"
    print(f"Input isolation: {snapshot['input_isolation_mode']} ({isolation_state})")
    print(
        "Runtime: "
        f"Sunshine={'active' if snapshot['sunshine_active'] else 'inactive'}, "
        f"Sway={'active' if snapshot['sway_active'] else 'inactive'}, "
        f"Input bridge={snapshot['bridge_state']}, "
        f"Audio guard={snapshot['audio_guard_state']}"
    )
    print(
        "Dynamic MangoHud FPS limit: "
        f"{'enabled' if snapshot['dynamic_mangohud_fps_limit'] else 'disabled'}"
    )
    print(f"MangoHud env value: {snapshot['current_mangohud_config'] or 'not active'}")
    print(f"Refresh rate sync mode: {snapshot['refresh_rate_sync_mode']}")
    if snapshot["refresh_rate_sync_mode"] == "custom":
        print(f"Custom display target: {snapshot['custom_display_mode_summary'] or 'not configured'}")
    print(f"Audio sink: {snapshot['audio_sink']}")
    print(f"Headless display: {snapshot['wayland_display'] or 'not detected'}")
    print(f"Current headless mode: {snapshot['current_headless_mode'] or 'not detected'}")
    if snapshot["input_isolation_mode"] == "kwin-runtime-disable":
        print(f"Host Sunshine inputs: {snapshot['sunshine_input_device_count']}")
        if snapshot["kwin_isolation_error"]:
            print(f"KWin isolation error: {snapshot['kwin_isolation_error']}")
        elif snapshot["kwin_isolation_state"] == "inactive":
            print("KWin-isolated devices: helper not running")
        elif snapshot["kwin_isolation_state"] == "starting":
            print("KWin-isolated devices: helper starting")
        elif snapshot["kwin_isolation_devices"]:
            print(
                "KWin-isolated devices: "
                f"{len(snapshot['kwin_isolation_devices'])} "
                f"(matched {snapshot['kwin_isolation_seen_device_count']})"
            )
        elif snapshot["sunshine_input_device_count"] > 0:
            print(
                "KWin-isolated devices: 0 "
                f"(matched {snapshot['kwin_isolation_seen_device_count']} of {snapshot['sunshine_input_device_count']})"
            )
        else:
            print("KWin-isolated devices: waiting for Sunshine virtual inputs")
        if snapshot["kwin_isolation_failed_devices"]:
            print(f"KWin isolation failures: {len(snapshot['kwin_isolation_failed_devices'])}")
    print(f"Portal handoff: {'active' if snapshot['portal_handoff_active'] else 'idle'}")
    print(f"Controllers: {snapshot['controller_count']} configured")
    if snapshot["controller_detection_error"]:
        print(f"Controller detection: {snapshot['controller_detection_error']}")
    elif snapshot["controllers"]:
        for controller in snapshot["controllers"]:
            suffix = f" - {controller['details']}" if controller["details"] else ""
            print(f"- {controller['label']} ({controller['state']}){suffix}")
    else:
        print("- Client inputs from Moonlight/Sunshine work automatically.")
        print("- No host controllers are currently reserved for passthrough.")
    print(f"Logs: {snapshot['last_launch_log_file']}")
    print(f"Next step: {snapshot['next_step']}")
    return 0


def display_logs(lines: int = 80) -> int:
    result = subprocess.run(
        [
            "journalctl",
            "--user",
            "-u",
            _sunshine_unit(),
            "-n",
            str(lines),
            "--no-pager",
        ],
        check=False,
    )
    return result.returncode


def remove_display() -> int:
    state = load_state()
    if not state.get("enabled"):
        print("Virtual display is not configured.")
        return 1

    stop_display()
    if not _remove_udev_rule(state):
        print("Warning: failed to remove the managed udev rule.")

    for path in _managed_setup_paths(state):
        try:
            path.unlink()
        except OSError:
            pass

    for path in _managed_override_paths(state):
        try:
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()
        except OSError:
            pass

    for path_key in [
        "portal_active_file",
        "portal_lock_file",
        "input_bridge_status_file",
        "kwin_input_isolation_status_file",
        "wayland_display_file",
        "audio_module_file",
    ]:
        try:
            path_value = state["paths"].get(path_key)
            if not path_value:
                continue
            Path(path_value).unlink()
        except OSError:
            pass

    try:
        Path(state["paths"]["state_path"]).unlink()
    except OSError:
        pass
    try:
        PROFILE_ROOT.rmdir()
    except OSError:
        pass
    try:
        BIN_ROOT.rmdir()
    except OSError:
        pass
    try:
        DISPLAY_ROOT.rmdir()
    except OSError:
        pass
    try:
        LEGACY_DISPLAY_ROOT.rmdir()
    except OSError:
        pass
    _daemon_reload()

    print("Virtual display setup removed.")
    return 0
