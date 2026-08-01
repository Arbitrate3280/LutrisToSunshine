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
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from utils.input import get_user_input, get_yes_no_input
from display import audio_policy
from display import sunshine_service as _svc
from display.utils import run_command, safe_string


PROFILE_NAME = "default"
CONFIG_ROOT = Path("~/.config/lutristosunshine").expanduser()
LEGACY_DISPLAY_DIRNAME = "virtual" + "display"
DISPLAY_ROOT = CONFIG_ROOT / "display"
LEGACY_DISPLAY_ROOT = CONFIG_ROOT / LEGACY_DISPLAY_DIRNAME
PROFILE_ROOT = DISPLAY_ROOT / PROFILE_NAME
BIN_ROOT = CONFIG_ROOT / "bin"
DISPLAY_STATE_PATH = DISPLAY_ROOT / "display.json"
LEGACY_STATE_PATH = LEGACY_DISPLAY_ROOT / f"{LEGACY_DISPLAY_DIRNAME}.json"
FLATPAK_PORTAL_UNIT = "flatpak-portal.service"
DISPLAY_SOCKET_PATH = f"/run/user/{os.getuid()}/lutristosunshine-display.sock"
WAYLAND_DISPLAY_PATH = PROFILE_ROOT / "wayland-display"
AUDIO_MODULE_PATH = PROFILE_ROOT / "audio-module-id"
PORTAL_LOCK_PATH = PROFILE_ROOT / "flatpak-portal.lock"
PORTAL_ACTIVE_PATH = PROFILE_ROOT / "flatpak-portal-active"
LAST_LAUNCH_LOG_PATH = PROFILE_ROOT / "last-launch.log"
UDEV_RULE_PATH = "/etc/udev/rules.d/85-lutristosunshine-sunshine-input.rules"
FALLBACK_WIDTH = 1920
FALLBACK_HEIGHT = 1080
FALLBACK_FPS = 60
REFRESH_RATE_SYNC_MODES = {"client", "exact", "custom"}
SUNSHINE_INPUT_VENDOR_ID = 0xBEEF
SUNSHINE_INPUT_PRODUCT_ID = 0xDEAD
SUNSHINE_INPUT_NAME_MARKERS = [
    "Keyboard_passthrough",
    "Mouse_passthrough",
    "Touch_passthrough",
    "Pen_passthrough",
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
]
FLATPAK_PORTAL_SWITCH_TIMEOUT = 20
FLATPAK_PORTAL_SPAWN_TIMEOUT = 15
FLATPAK_PORTAL_RESTORE_GRACE = 2


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


def _resolve_sunshine_config_root(unit_name: str) -> Path:
    if unit_name and unit_name.startswith("app-"):
        flatpak_id = unit_name.removeprefix("app-").removesuffix(".service")
        return Path.home() / ".var" / "app" / flatpak_id / "config" / "sunshine"
    return detect_sunshine_config_root()


def _normalized_refresh_rate_sync_mode(value: Any) -> str:
    normalized = safe_string(value).lower()
    if normalized in REFRESH_RATE_SYNC_MODES:
        return normalized
    return "client"


def _refresh_rate_sync_mode_summary(mode: str) -> str:
    if mode == "exact":
        return "client's refresh rate"
    if mode == "custom":
        return "custom fixed display mode"
    return "follow Moonlight requested FPS"


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
        key = safe_string(key)
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
    sway_socket = safe_string(state.get("sway_socket"))
    if not sway_socket or not Path(sway_socket).exists():
        return ""
    result = run_command(
        ["swaymsg", "-s", sway_socket, "-t", "get_outputs", "-r"],
        check=False,
    )
    if result.returncode != 0 or not safe_string(result.stdout):
        return ""
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, list):
        return ""
    for output in payload:
        if not isinstance(output, dict) or safe_string(output.get("name")) != "HEADLESS-1":
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


def _resolve_sunshine_unit(state: Dict[str, Any]) -> str:
    """Return the Sunshine unit to operate on.

    Prefers the unit saved in state; falls back to live detection.
    """
    return safe_string(state.get("sunshine_unit_name")) or _svc.sunshine_unit()


def _sunshine_verb(
    state: Dict[str, Any],
    verb_fn: Callable[[str], subprocess.CompletedProcess],
) -> subprocess.CompletedProcess:
    """Run a systemd verb (restart/stop) on the Sunshine unit and print stderr on failure."""
    result = verb_fn(_resolve_sunshine_unit(state))
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if stderr:
            print(stderr)
    return result


def _state_paths(unit_name: str) -> Dict[str, str]:
    systemd_user_dir = Path("~/.config/systemd/user").expanduser()
    override_dir = systemd_user_dir / f"{unit_name}.d"
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
        "wireplumber_policy_script": str(audio_policy.WIREPLUMBER_SCRIPTS_DIR / audio_policy.WIREPLUMBER_POLICY_SCRIPT_NAME),
        "wireplumber_policy_conf": str(audio_policy.WIREPLUMBER_CONF_DIR / audio_policy.WIREPLUMBER_POLICY_CONF_NAME),
        "launch_app_script": str(BIN_ROOT / "lutristosunshine-launch-app.sh"),
        "resolve_stream_fps_script": str(BIN_ROOT / "lutristosunshine-resolve-stream-fps.sh"),
        "apply_exact_refresh_script": str(BIN_ROOT / "lutristosunshine-apply-exact-refresh.sh"),
        "headless_prep_script": str(BIN_ROOT / "lutristosunshine-run-headless-prep.sh"),
        "set_resolution_script": str(BIN_ROOT / "lutristosunshine-set-resolution.sh"),
        "reset_resolution_script": str(BIN_ROOT / "lutristosunshine-reset-resolution.sh"),
        "get_gpu_addr": str(BIN_ROOT / "lutristosunshine-get-gpu-addr.sh"),
        "portal_lock_file": str(PORTAL_LOCK_PATH),
        "portal_active_file": str(PORTAL_ACTIVE_PATH),
        "last_launch_log_file": str(LAST_LAUNCH_LOG_PATH),
        "wayland_display_file": str(WAYLAND_DISPLAY_PATH),
        "audio_module_file": str(AUDIO_MODULE_PATH),
        "systemd_user_dir": str(systemd_user_dir),
        "sunshine_override_dir": str(override_dir),
        "sunshine_override": str(override_dir / "override.conf"),
        "kwin_input_isolation_script": str(BIN_ROOT / "lutristosunshine-kwin-input-isolation.py"),
        "sunshine_conf": str(_resolve_sunshine_config_root(unit_name) / "sunshine.conf"),
        "kwin_input_isolation_status_file": str(PROFILE_ROOT / "kwin-input-isolation-status.json"),
    }


def _default_state() -> Dict[str, Any]:
    state = {
        "enabled": False,
        "dynamic_mangohud_fps_limit": False,
        "refresh_rate_sync_mode": "client",
        "custom_display_mode": _normalized_custom_display_mode(None),
        "sunshine_execstart": "",
        "sunshine_unit_name": "",
        "profile": PROFILE_NAME,
        "audio_sink": "lts-sunshine-stereo",
        "sway_socket": DISPLAY_SOCKET_PATH,
        "udev_rule_path": UDEV_RULE_PATH,
        "sunshine_audio_sink": None,
        "gpu_mode": "auto",
        "gpu_card_path": "",
        "gpu_render_path": "",
        "renderer_mode": "default",
    }
    state["paths"] = _state_paths(_svc.sunshine_unit())
    return state


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
    if not safe_string(state.get("sunshine_unit_name")):
        state["sunshine_unit_name"] = _svc.sunshine_unit()
    state["refresh_rate_sync_mode"] = _normalized_refresh_rate_sync_mode(
        state.get("refresh_rate_sync_mode")
    )
    state["custom_display_mode"] = _normalized_custom_display_mode(
        state.get("custom_display_mode")
    )
    state["gpu_mode"] = safe_string(state.get("gpu_mode")).lower() if safe_string(state.get("gpu_mode")).lower() in {"auto", "manual"} else "auto"
    state["gpu_card_path"] = safe_string(state.get("gpu_card_path"))
    state["gpu_render_path"] = safe_string(state.get("gpu_render_path"))
    state["renderer_mode"] = safe_string(state.get("renderer_mode")).lower() if safe_string(state.get("renderer_mode")).lower() in {"default", "vulkan"} else "default"
    state["paths"] = _state_paths(state["sunshine_unit_name"])
    return state


def save_state(state: Dict[str, Any]) -> None:
    DISPLAY_ROOT.mkdir(parents=True, exist_ok=True)
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


def set_dynamic_mangohud_fps_limit(enabled: bool) -> Tuple[bool, Dict[str, Any]]:
    state = load_state()
    previous = bool(state.get("dynamic_mangohud_fps_limit"))
    state["dynamic_mangohud_fps_limit"] = bool(enabled)
    return previous, refresh_managed_files(state)


def set_refresh_rate_sync_mode(mode: str) -> Tuple[str, Dict[str, Any]]:
    state = load_state()
    previous = _normalized_refresh_rate_sync_mode(state.get("refresh_rate_sync_mode"))
    state["refresh_rate_sync_mode"] = _normalized_refresh_rate_sync_mode(mode)
    return previous, refresh_managed_files(state)


def set_custom_display_mode(width: int, height: int, refresh: float) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    state = load_state()
    previous = _normalized_custom_display_mode(state.get("custom_display_mode"))
    state["custom_display_mode"] = _normalized_custom_display_mode(
        {
            "width": width,
            "height": height,
            "refresh": refresh,
        }
    )
    return previous, refresh_managed_files(state)

_PCI_IDS_PATHS = [
    Path("/usr/share/hwdata/pci.ids"),
    Path("/usr/share/pci.ids"),
]


def _pci_device_name(vendor_hex: str, device_hex: str) -> str:
    """Look up the human-readable device name from pci.ids using vendor/device IDs.
    vendor_hex/device_hex are like '0x1002' / '0x73df'. Returns '' if not found.
    """
    pci_ids_file = None
    for candidate in _PCI_IDS_PATHS:
        if candidate.exists():
            pci_ids_file = candidate
            break
    if pci_ids_file is None:
        return ""
    vendor_prefix = vendor_hex.replace("0x", "").lower()
    device_prefix = device_hex.replace("0x", "").lower()
    if not vendor_prefix or not device_prefix:
        return ""
    try:
        in_vendor = False
        with open(pci_ids_file, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not in_vendor:
                    if line.rstrip() and not line[0].isspace() and line.split()[0].lower() == vendor_prefix:
                        in_vendor = True
                    continue
                if line.strip() and not line[0].isspace() and not line.startswith("#"):
                    break
                if line[0] == "\t" and (len(line) < 2 or line[1] != "\t"):
                    parts = line.lstrip("\t").split(None, 1)
                    if parts and parts[0].lower() == device_prefix:
                        return parts[1].strip() if len(parts) > 1 else ""
        return ""
    except OSError:
        return ""


def _detect_available_gpus() -> List[Dict[str, str]]:
    """Detect available DRM GPUs from /sys/class/drm/card*.
    Returns a list of dicts with keys: pci_addr, vendor, model, label, card_path, render_path.
    """
    gpus = []
    for entry in sorted(Path("/sys/class/drm").glob("card*")):
        if not re.match(r"^card\d+$", entry.name):
            continue
        try:
            device_link = entry / "device"
            if not device_link.is_symlink():
                continue
            pci_addr = os.path.basename(os.path.realpath(str(device_link)))
            if not pci_addr.startswith("0000:"):
                continue
            vendor_file = entry / "device" / "vendor"
            device_file = entry / "device" / "device"
            if not vendor_file.exists():
                continue
            vendor_id = vendor_file.read_text().strip()
            device_id = device_file.read_text().strip() if device_file.exists() else ""
        except OSError:
            continue
        vendor = "Unknown"
        gpu_type = "Unknown"
        if vendor_id == "0x8086":
            vendor = "Intel"
            gpu_type = "Integrated" if pci_addr == "0000:00:02.0" else "Discrete"
        elif vendor_id == "0x1002":
            vendor = "AMD"
            hwmon_dir = entry / "device" / "hwmon"
            try:
                has_voltage = any(hwmon_dir.glob("hwmon*/in1_input"))
            except OSError:
                has_voltage = False
            gpu_type = "Integrated" if has_voltage else "Discrete"
        elif vendor_id == "0x10de":
            vendor = "NVIDIA"
            gpu_type = "Discrete"
        card_path = f"/dev/dri/by-path/pci-{pci_addr}-card"
        render_path = f"/dev/dri/by-path/pci-{pci_addr}-render"
        model = _pci_device_name(vendor_id, device_id)
        if model:
            label = f"{vendor} {model}"
        else:
            label = f"{vendor} ({gpu_type}) [{pci_addr}]"
        gpus.append({
            "pci_addr": pci_addr,
            "vendor": vendor,
            "model": model,
            "label": label,
            "card_path": card_path,
            "render_path": render_path,
        })
    return gpus


def gpu_status_label(state: Dict[str, Any]) -> str:
    """Return a human-readable label for the current GPU mode setting."""
    gpu_mode = safe_string(state.get("gpu_mode")).lower()
    if gpu_mode != "manual":
        return "[AUTO] wlroots chooses GPU"
    card_path = safe_string(state.get("gpu_card_path"))
    if not card_path:
        return "[AUTO] wlroots chooses GPU"
    card_exists = Path(card_path).exists()
    if not card_exists:
        return f"[STALE] {card_path} (path no longer exists, falling back to auto)"
    pci_addr = ""
    by_path_prefix = "/dev/dri/by-path/pci-"
    if card_path.startswith(by_path_prefix) and card_path.endswith("-card"):
        pci_addr = card_path[len(by_path_prefix):-len("-card")]
    gpus = _detect_available_gpus()
    for gpu in gpus:
        if gpu["pci_addr"] == pci_addr:
            return f"[MANUAL] {gpu['label']} ({card_path})"
    return f"[MANUAL] {card_path}"


def set_gpu_mode(mode: str, card_path: str = "", render_path: str = "") -> Dict[str, Any]:
    state = load_state()
    if mode not in ("auto", "manual"):
        mode = "auto"
    state["gpu_mode"] = mode
    if mode == "manual" and card_path:
        state["gpu_card_path"] = card_path
        state["gpu_render_path"] = render_path
    else:
        state["gpu_card_path"] = ""
        state["gpu_render_path"] = ""
    return refresh_managed_files(state)


def configure_gpu() -> int:
    """Interactive GPU selection for the virtual display."""
    state = load_state()
    print("")
    print("Virtual display GPU selection")
    print("Pick which graphics card drives the virtual display.")
    print("Most users should leave this on Auto.")
    current_label = gpu_status_label(state)
    print(f"Current: {current_label}")
    print("")
    print("  0. Auto — let the system decide")
    gpus = _detect_available_gpus()
    if not gpus:
        print("")
        print("No DRM GPUs detected on this system.")
        gpu_mode = safe_string(state.get("gpu_mode")).lower()
        if gpu_mode == "manual":
            print("Current manual GPU selection will be cleared to Auto.")
            if get_user_input(
                "Reset GPU selection to Auto? (y/n): ",
                lambda value: value.strip().lower() if value.strip().lower() in {"y", "n"} else (_ for _ in ()).throw(ValueError()),
                "Enter y or n.",
            ) == "y":
                set_gpu_mode("auto")
                print("GPU selection reset to Auto.")
        return 0
    valid_choices = ["0"]
    for i, gpu in enumerate(gpus, start=1):
        card_exists = "OK" if Path(gpu["card_path"]).exists() else "MISSING"
        print(f"  {i}. {gpu['label']} — {gpu['card_path']} ({card_exists})")
        valid_choices.append(str(i))
    print("")
    choice = get_user_input(
        "Choose a GPU for the virtual display: ",
        lambda value: value.strip() if value.strip() in valid_choices else (_ for _ in ()).throw(ValueError()),
        f"Enter a number from 0 to {len(gpus)}.",
    )
    if choice == "0":
        set_gpu_mode("auto")
        print("GPU selection set to Auto.")
    else:
        idx = int(choice) - 1
        gpu = gpus[idx]
        set_gpu_mode("manual", gpu["card_path"], gpu["render_path"])
        print(f"GPU selection set to {gpu['label']} ({gpu['card_path']}).")

    print("")
    if get_yes_no_input("Restart the virtual display for the change to take effect?", default=True):
        return restart_display()
    return 0


def renderer_status_label(state: Dict[str, Any]) -> str:
    mode = safe_string(state.get("renderer_mode")).lower()
    if mode == "vulkan":
        return "[VULKAN] HDR capable, may have less GPU support"
    return "[DEFAULT] GLES2 — stable, broad GPU support, no HDR"


def set_renderer_mode(mode: str) -> Dict[str, Any]:
    state = load_state()
    if mode not in ("default", "vulkan"):
        mode = "default"
    state["renderer_mode"] = mode
    return refresh_managed_files(state)


def configure_renderer_mode() -> int:
    state = load_state()
    print("")
    print("Virtual display renderer")
    print("Controls how graphics are drawn on the virtual display.")
    print("GLES2: works on most GPUs, no HDR.")
    print("Vulkan: HDR capable, may not work on all GPUs.")
    current = safe_string(state.get("renderer_mode")).lower()
    print(f"Current: {'[VULKAN] HDR capable, may have less GPU support' if current == 'vulkan' else '[DEFAULT] GLES2 — stable, broad GPU support, no HDR'}")
    print("")
    print("  0. GLES2 — stable, broad GPU support (no HDR)")
    print("  1. Vulkan — HDR pass-through (may have less GPU support)")
    print("")
    choice = get_user_input(
        "Choose renderer mode: ",
        lambda value: value.strip() if value.strip() in {"0", "1"} else (_ for _ in ()).throw(ValueError()),
        "Enter 0 for GLES2 or 1 for Vulkan.",
    )
    if choice == "0":
        set_renderer_mode("default")
        print("Renderer set to GLES2. Stable and broad GPU support, no HDR.")
    else:
        set_renderer_mode("vulkan")
        print("Renderer set to Vulkan. HDR pass-through enabled (may have less GPU support).")

    print("")
    if get_yes_no_input("Restart the virtual display for the change to take effect?", default=True):
        return restart_display()
    return 0


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
    result = run_command(lookup_command, check=False)
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


def _parse_systemd_execstart(value: str) -> str:
    raw_value = safe_string(value)
    if not raw_value:
        return ""

    for line in raw_value.splitlines():
        match = re.search(r"argv\[]=(.*?) ;", line)
        if match:
            return safe_string(match.group(1))

    if "argv[]=" in raw_value:
        match = re.search(r"argv\[]=(.*)", raw_value, re.DOTALL)
        if match:
            return safe_string(match.group(1))

    return raw_value


def _current_sunshine_execstart(unit: str) -> str:
    return _parse_systemd_execstart(_svc.show_unit_property(unit, "ExecStart"))


def _fragment_sunshine_execstart(unit: str) -> str:
    fragment_path = Path(safe_string(_svc.show_unit_property(unit, "FragmentPath")))
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
            execstart = safe_string(line.split("=", 1)[1])
            if execstart:
                return execstart
    except OSError:
        return ""

    return ""


def _remember_sunshine_execstart(state: Dict[str, Any]) -> Dict[str, Any]:
    target_unit = _resolve_sunshine_unit(state)
    current_execstart = _current_sunshine_execstart(target_unit) or _fragment_sunshine_execstart(target_unit)
    wrapper_path = safe_string(state.get("paths", {}).get("sunshine_wrapper_script"))

    if current_execstart and wrapper_path and current_execstart != wrapper_path:
        state["sunshine_execstart"] = current_execstart
        return state

    if safe_string(state.get("sunshine_execstart")):
        return state

    state["sunshine_execstart"] = _svc.sunshine_binary() or "sunshine"
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
    result = run_command(prefix + command)
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
        result = run_command(command)
        if result.returncode != 0:
            return False
    return True


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


def _host_session_name() -> str:
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
    status["state"] = safe_string(status.get("state")) or "inactive"
    status["service"] = safe_string(status.get("service"))
    status["last_error"] = safe_string(status.get("last_error"))
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


def _clean_kde_libinput_config() -> None:
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


def _ensure_dependencies() -> List[str]:
    missing = []
    for binary in ["flock", "gdbus", "pactl", "python3", "setfacl", "stdbuf", "sway", "swaybg", "swaymsg", "systemctl"]:
        if shutil.which(binary) is None:
            missing.append(binary)
    if _svc.sunshine_binary() is None and not _current_sunshine_execstart(_svc.sunshine_unit()):
        missing.append("sunshine")
    return missing


def _write_managed_files(state: Dict[str, Any]) -> None:
    PROFILE_ROOT.mkdir(parents=True, exist_ok=True)
    BIN_ROOT.mkdir(parents=True, exist_ok=True)
    Path(state["paths"]["systemd_user_dir"]).mkdir(parents=True, exist_ok=True)

    from display import scripts_render  # lazy: scripts_render imports this module

    for path, content in scripts_render.render_managed_files(state).items():
        _write_file(path, content, executable=path.suffix == ".sh")
    for path, content in audio_policy.managed_files(state).items():
        _write_file(path, content, executable=path.suffix == ".sh")


def refresh_managed_files(state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if state is None:
        state = load_state()
    if not safe_string(state.get("sunshine_execstart")):
        state = _remember_sunshine_execstart(state)
    _write_managed_files(state)
    save_state(state)
    _daemon_reload()
    return state


def _managed_setup_paths(state: Dict[str, Any]) -> List[Path]:
    paths = state["paths"]
    keys = [
        "sway_config",
        "sway_start_script",
        "sunshine_start_script",
        "sunshine_wrapper_script",
        "audio_create_script",
        "audio_cleanup_script",
        "wireplumber_policy_script",
        "wireplumber_policy_conf",
        "launch_app_script",
        "resolve_stream_fps_script",
        "apply_exact_refresh_script",
        "headless_prep_script",
        "set_resolution_script",
        "reset_resolution_script",
        "kwin_input_isolation_script",
        "get_gpu_addr",
    ]
    return [Path(paths[key]) for key in keys if safe_string(paths.get(key))]


def _daemon_reload() -> None:
    _svc.daemon_reload()


def setup_display() -> int:
    missing = _ensure_dependencies()
    if missing:
        print("Missing required commands:", ", ".join(missing))
        return 1

    state = load_state()
    sunshine_was_active = _svc.is_sunshine_service_active()

    # Re-detect and persist the unit name so overrides go to the right directory,
    # even if the user switched Sunshine installations since the last run.
    state["sunshine_unit_name"] = _svc.sunshine_unit()
    state["paths"] = _state_paths(state["sunshine_unit_name"])

    state = _remember_sunshine_execstart(state)
    state = refresh_managed_files(state)
    audio_policy.setup(state)
    save_state(state)

    if not _install_udev_rule(state):
        audio_policy.stop(state)
        state["sunshine_audio_sink"] = None
        audio_policy.remove(state)
        save_state(state)
        print("Error: unable to install the Sunshine input isolation udev rule.")
        print("Install sudo or pkexec, then rerun the command.")
        return 1

    _daemon_reload()
    state["enabled"] = True
    save_state(state)
    if sunshine_was_active:
        result = _sunshine_verb(state, _svc.restart_sunshine_unit)
        if result.returncode != 0:
            print("Error: failed to restart Sunshine after installing virtual display files.")
            return 1

    print("Virtual display files installed.")
    return 0


def start_display() -> int:
    state = load_state()
    if not state.get("enabled"):
        print("Virtual display is not set up. Run 'python3 lutristosunshine.py display enable' first.")
        return 1
    state = refresh_managed_files(state)
    audio_policy.start(state)
    save_state(state)
    sunshine_unit = _resolve_sunshine_unit(state)
    result = _svc.start_sunshine_unit(sunshine_unit)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        _svc.stop_sunshine_unit(sunshine_unit)
        audio_policy.stop(state)
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
    result = stop_display()
    if result != 0:
        return result
    return start_display()


def stop_display() -> int:
    state = load_state()
    if not state.get("enabled"):
        print("Virtual display is not set up.")
        return 1
    result = _sunshine_verb(state, _svc.stop_sunshine_unit)
    if result.returncode != 0:
        return 1
    try:
        Path(state["paths"]["kwin_input_isolation_status_file"]).unlink()
    except OSError:
        pass
    audio_policy.stop(state)
    save_state(state)
    print("Virtual display stopped.")
    return 0


def display_snapshot() -> Dict[str, Any]:
    state = load_state()
    configured = bool(state.get("enabled"))
    custom_mode = _normalized_custom_display_mode(state.get("custom_display_mode"))
    active_launch_status = _active_launch_status(state)
    sunshine_active = _svc.is_sunshine_service_active() if configured else False
    host_session = _host_session_name()
    input_isolation_mode = _input_isolation_mode()
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
    wp_policy_state = "installed" if Path(state["paths"]["wireplumber_policy_conf"]).exists() else "absent"
    kwin_status = _kwin_input_isolation_status(state) if configured else _empty_kwin_input_isolation_status()
    sunshine_input_devices = _sunshine_virtual_input_devices() if configured else []

    snapshot = {
        "configured": configured,
        "dynamic_mangohud_fps_limit": bool(state.get("dynamic_mangohud_fps_limit")),
        "refresh_rate_sync_mode": _normalized_refresh_rate_sync_mode(state.get("refresh_rate_sync_mode")),
        "custom_display_mode": custom_mode,
        "custom_display_mode_summary": _custom_display_mode_string(custom_mode),
        "profile": state["profile"],
        "host_session": host_session,
        "input_isolation_mode": input_isolation_mode,
        # Resolved via state (saved or live-detected) so the snapshot shows
        # the unit the display lifecycle would actually operate on.
        "sunshine_unit": _resolve_sunshine_unit(state),
        "sunshine_install_audit": _svc.sunshine_installation_audit(_resolve_sunshine_unit(state)),
        "sunshine_active": sunshine_active,
        "sway_active": sway_active,
        "wireplumber_policy": wp_policy_state,
        "audio_sink": state["audio_sink"],
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
        "current_mangohud_config": safe_string(active_launch_status.get("mangohud_config")),
        "last_launch_log_file": state["paths"]["last_launch_log_file"],
        "dependencies_missing": _ensure_dependencies(),
        "gpu_mode": safe_string(state.get("gpu_mode")).lower() if safe_string(state.get("gpu_mode")).lower() in {"auto", "manual"} else "auto",
        "gpu_card_path": safe_string(state.get("gpu_card_path")),
        "gpu_render_path": safe_string(state.get("gpu_render_path")),
        "gpu_status_label": gpu_status_label(state),
        "renderer_mode": safe_string(state.get("renderer_mode")).lower() if safe_string(state.get("renderer_mode")).lower() in {"default", "vulkan"} else "default",
        "renderer_status_label": renderer_status_label(state),
        "next_step": "",
    }
    if not configured:
        snapshot["next_step"] = "Run 'python3 lutristosunshine.py display enable' to set up the headless stack."
    elif not sunshine_active:
        snapshot["next_step"] = "Run 'python3 lutristosunshine.py display start' to start the managed stack."
    elif not sway_active:
        snapshot["next_step"] = "Run 'python3 lutristosunshine.py display status' or '... display logs' to inspect why headless Sway is not ready."
    else:
        snapshot["next_step"] = "Virtual display is ready. Use 'status' or 'logs' for follow-up actions."

    if snapshot["dependencies_missing"] or not configured:
        status_summary = "NOT SET UP"
    elif sunshine_active and sway_active:
        status_summary = "READY"
    elif sunshine_active or sway_active:
        status_summary = "PARTIAL"
    else:
        status_summary = "STOPPED"
    snapshot["status_summary"] = status_summary

    mode_summary = _refresh_rate_sync_mode_summary(snapshot["refresh_rate_sync_mode"])
    if snapshot["refresh_rate_sync_mode"] == "custom":
        mode_summary = f"{mode_summary} ({_custom_display_mode_string(custom_mode)})"
    snapshot["display_sync_summary"] = (
        f"{mode_summary}; MangoHud FPS sync "
        f"{'on' if snapshot['dynamic_mangohud_fps_limit'] else 'off'}"
    )
    snapshot["refresh_rate_sync_mode_summary"] = _refresh_rate_sync_mode_summary(
        snapshot["refresh_rate_sync_mode"]
    )

    isolation_level = "success"
    isolation_summary = "rule ready"
    if input_isolation_mode == "kwin-runtime-disable":
        if kwin_status["last_error"]:
            isolation_level = "error"
            isolation_summary = kwin_status["last_error"]
        elif kwin_status["state"] == "inactive":
            isolation_level = "warning"
            isolation_summary = "KWin helper is not running"
        elif kwin_status["state"] == "starting":
            isolation_level = "info"
            isolation_summary = "KWin helper is starting"
        elif kwin_status["disabled_devices"]:
            isolation_summary = (
                f"disabled {len(kwin_status['disabled_devices'])} of "
                f"{len(sunshine_input_devices)} Sunshine device(s) in KWin"
            )
        elif len(sunshine_input_devices) > 0:
            isolation_level = "warning"
            isolation_summary = (
                f"matched {kwin_status['seen_device_count']} of "
                f"{len(sunshine_input_devices)} Sunshine device(s), but disabled 0"
            )
        else:
            isolation_level = "warning"
            isolation_summary = "waiting for Sunshine virtual inputs"
    snapshot["isolation_level"] = isolation_level
    snapshot["isolation_summary"] = isolation_summary
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

    audit = snapshot["sunshine_install_audit"]
    detected_types = audit.get("detected_types") or []
    managed_type = safe_string(audit.get("managed_type"))
    package_probe_type = safe_string(audit.get("package_probe_type"))
    resolved_type = safe_string(audit.get("resolved_type"))
    if len(detected_types) > 1 and managed_type and managed_type != "unknown" and package_probe_type and package_probe_type != managed_type:
        checks.append(
            {
                "label": "Sunshine installation",
                "status": "warn",
                "message": (
                    f"Multiple Sunshine installs detected ({', '.join(detected_types)}). "
                    f"Virtual display manages {audit['managed_unit']} ({managed_type}) and resolves Sunshine as {resolved_type}; "
                    f"package-only probing would resolve {package_probe_type}."
                ),
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
        f"WP policy={snapshot['wireplumber_policy']}"
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
    print(f"Logs: {snapshot['last_launch_log_file']}")
    print(f"Virtual display GPU: {snapshot['gpu_status_label']}")
    print(f"Display renderer: {snapshot['renderer_status_label']}")
    print(f"Next step: {snapshot['next_step']}")
    return 0


def display_logs(lines: int = 80) -> int:
    return _svc.fetch_sunshine_journal(lines).returncode


def remove_display() -> int:
    state = load_state()
    if not state.get("enabled"):
        print("Virtual display is not configured.")
        return 1

    stop_result = stop_display()
    if stop_result != 0:
        print("Warning: could not stop the managed Sunshine service; continuing with file cleanup.")
    if not _remove_udev_rule(state):
        print("Warning: failed to remove the managed udev rule.")
    _clean_kde_libinput_config()
    audio_policy.remove(state)

    for path in _managed_setup_paths(state):
        try:
            path.unlink()
        except OSError:
            pass

    _svc.cleanup_managed_overrides(state)

    for path_key in [
        "portal_active_file",
        "portal_lock_file",
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
