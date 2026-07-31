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
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from utils.input import get_user_input, get_yes_no_input
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
# LutrisToSunshine audio policy lives in WirePlumber (event-driven, host-level)
# rather than a polling bash guard. WP 0.5+ loads user scripts from XDG_DATA and
# merges XDG_CONFIG fragments.
WIREPLUMBER_SCRIPTS_DIR = Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser() / "wireplumber" / "scripts"
WIREPLUMBER_CONF_DIR = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "wireplumber" / "wireplumber.conf.d"
WIREPLUMBER_POLICY_SCRIPT_NAME = "lts-audio-policy.lua"
WIREPLUMBER_POLICY_CONF_NAME = "54-lts-audio-policy.conf"
AUDIO_STREAM_MARKER_KEY = "lutristosunshine.stream"
AUDIO_STREAM_MARKER_VALUE = "game"
AUDIO_STREAM_PULSE_PROP = f"{AUDIO_STREAM_MARKER_KEY}={AUDIO_STREAM_MARKER_VALUE}"
AUDIO_STREAM_PIPEWIRE_PROPS = f'{{ {AUDIO_STREAM_MARKER_KEY} = "{AUDIO_STREAM_MARKER_VALUE}" }}'
HEADLESS_PREP_PREFIX = "headless:"
SUNSHINE_OWNED_SINK_NAMES = [
    "sink-sunshine-stereo",
    "sink-sunshine-surround51",
    "sink-sunshine-surround71",
]
PIPEWIRE_VIRTUAL_SINK_NAMES = [
    "auto_null",
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
# ponytail: shell-side flatpak option parser.  @FLAGS@ and @VALUE_OPTS@ are
# rendered at script-generation time from the same FLATPAK_FLAG_OPTIONS /
# FLATPAK_VALUE_OPTIONS constants that _parse_flatpak_run_command uses.
# If the parser's option sets change, this heredoc picks up the change
# automatically via the shared constants — but the two parsers' logic
# (option boundary, --env handling) must stay in sync.
_INJECT_FLATPAK_AUDIO_ENV_HEREDOC = """\
inject_flatpak_audio_env() {
    python3 - "$1" "@AUDIO_SINK@" "@PULSE_PROP@" '@PIPEWIRE_PROPS@' <<'PY'
import shlex
import sys

cmd = sys.argv[1]
sink = sys.argv[2]
prop = sys.argv[3]
pwprop = sys.argv[4]

AUDIO_PREFIXES = ("PULSE_SINK=", "PULSE_PROP=", "PIPEWIRE_PROPS=")
FLAGS = frozenset(@FLAGS@)
VALUE_OPTS = frozenset(@VALUE_OPTS@)
PREFIX_OPTS = frozenset(f"{opt}=" for opt in VALUE_OPTS)

try:
    tokens = shlex.split(cmd)
except ValueError:
    print(cmd)
    raise SystemExit(0)

prefix = ["flatpak-spawn", "--host"]
index = len(prefix) if tokens[: len(prefix)] == prefix else 0
if tokens[index : index + 2] != ["flatpak", "run"]:
    print(cmd)
    raise SystemExit(0)
index += 2

# Walk options using flatpak's own flag/value/prefix classification.
# Only strip audio --env tokens from the option region (before app_id);
# app_id and app_args pass through untouched.  For example, in
#   flatpak run --env PULSE_SINK=custom org.foo --env PULSE_SINK=bar
# the first --env PULSE_SINK=custom is a Flatpak option (stripped);
# --env PULSE_SINK=bar after org.foo is an app argument (kept).
filtered = list(tokens[:index])  # prefix + flatpak + run
i = index
while i < len(tokens):
    tok = tokens[i]
    if not tok.startswith("-"):
        break  # app_id
    if tok == "--":
        break  # separator -> app_id follows
    if tok in FLAGS:
        filtered.append(tok)
        i += 1
        continue
    if tok in VALUE_OPTS:
        if i + 1 < len(tokens):
            value = tokens[i + 1]
            if tok == "--env" and any(
                value.startswith(p) for p in AUDIO_PREFIXES
            ):
                i += 2  # skip both --env and its audio value
            else:
                filtered.append(tok)
                filtered.append(value)
                i += 2
        else:
            filtered.append(tok)
            i += 1
        continue
    matched = next(
        (p for p in PREFIX_OPTS if tok.startswith(p)), None
    )
    if matched:
        if tok.startswith("--env=") and any(
            tok[6:].startswith(p) for p in AUDIO_PREFIXES
        ):
            pass  # skip audio prefix option
        else:
            filtered.append(tok)
        i += 1
        continue
    break  # unknown -> app_id boundary

# Inject the managed audio tuple and game marker right after
# "flatpak run", then append app_id and any remaining app_args.
inject = [
    "--env=PULSE_SINK=" + sink,
    "--env=PULSE_PROP=" + prop,
    "--env=PIPEWIRE_PROPS=" + pwprop,
]
print(shlex.join(filtered[:index] + inject + filtered[index:] + tokens[i:]))
PY
}
"""
_CLEAR_AUDIO_ACTIVATION_ENV = """\
    # Replace stale audio values inherited from a prior session.
    if command -v dbus-update-activation-environment >/dev/null 2>&1; then
        dbus-update-activation-environment --systemd \
            PULSE_SINK= PULSE_PROP= PIPEWIRE_PROPS= >/dev/null 2>&1 || true
    fi
    systemctl --user unset-environment PULSE_SINK PULSE_PROP PIPEWIRE_PROPS >/dev/null 2>&1 || true"""


def _rendered_inject_heredoc(heredoc: str, audio_sink: str) -> str:
    """Substitute audio-env placeholders in a pre-rendered inject heredoc."""
    return (
        heredoc.replace("@AUDIO_SINK@", audio_sink)
        .replace("@PULSE_PROP@", AUDIO_STREAM_PULSE_PROP)
        .replace("@PIPEWIRE_PROPS@", AUDIO_STREAM_PIPEWIRE_PROPS)
    )


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
        "wireplumber_policy_script": str(WIREPLUMBER_SCRIPTS_DIR / WIREPLUMBER_POLICY_SCRIPT_NAME),
        "wireplumber_policy_conf": str(WIREPLUMBER_CONF_DIR / WIREPLUMBER_POLICY_CONF_NAME),
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


def _pactl_list_short(entity: str) -> str:
    """Return raw output of `pactl list short <entity>`."""
    probe_env = dict(os.environ)
    probe_env["LANG"] = "C"
    probe_env["LC_ALL"] = "C"
    try:
        result = subprocess.run(
            ["pactl", "list", "short", entity],
            text=True, capture_output=True, check=False, timeout=5,
            env=probe_env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return safe_string(result.stdout or "")


def _find_hardware_sink(exclude_names: List[str]) -> str:
    """Return the first non-managed sink name from pactl."""
    skip = set(exclude_names) | set(PIPEWIRE_VIRTUAL_SINK_NAMES)
    for line in _pactl_list_short("sinks").strip().split("\n"):
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1] not in skip:
            return parts[1]
    return ""


def _find_hardware_source(exclude_names: List[str]) -> str:
    """Return the first non-managed input source name from pactl.
    Skips monitor sources (.monitor) since those capture playback of an
    output sink rather than an actual input device like a microphone.
    """
    skip = set(exclude_names) | {f"{s}.monitor" for s in PIPEWIRE_VIRTUAL_SINK_NAMES}
    for line in _pactl_list_short("sources").strip().split("\n"):
        parts = line.split("\t")
        if (
            len(parts) >= 2
            and parts[1] not in skip
            and ".monitor" not in parts[1]
        ):
            return parts[1]
    return ""


_WIREPLUMBER_POLICY_SCRIPT_TEMPLATE = """-- WirePlumber policy: LutrisToSunshine audio enforcement.
--
-- Sunshine forces the system default sink to its capture sink at stream start
-- (src/platform/linux/audio.cpp :: set_sink -> pa_context_set_default_sink) and
-- there is no Sunshine config to disable it; LTS instead routes the game per-app
-- (PULSE_SINK) and captures the managed sink's monitor, so the system default
-- must stay on host hardware. This script enforces the invariants declaratively,
-- in-process and event-driven (no polling, no subprocesses):
--   1. The default sink is never a managed sink (select-default-node hook).
--   2. A game stream (lutristosunshine.stream=game) targets the managed sink.
--   3. Sunshine's recorder targets the managed sink's monitor.

local log = Log.open_topic("s-lts-audio")

local audio_sink = "__AUDIO_SINK__"
local managed_sinks = {
__MANAGED_SINKS_TABLE__}

SimpleEventHook {
  name = "lts-audio/guard-default-sink",
  after = { "default-nodes/find-best-default-node",
            "default-nodes/find-selected-default-node",
            "default-nodes/find-stored-default-node" },
  before = { "default-nodes/apply-default-node" },
  interests = {
    EventInterest {
      Constraint { "event.type", "=", "select-default-node" },
    },
  },
  execute = function (event)
    local props = event:get_properties ()
    if props ["default-node.type"] ~= "audio.sink" then return end

    local selected = event:get_data ("selected-node")
    if type (selected) ~= "string" and selected then
      local ok, v = pcall (function () return selected:parse () end)
      if ok then selected = v end
    end

    if selected and managed_sinks [selected] then
      local om = event:get_source ():call ("get-object-manager", "node")
      local best_name, best_prio = nil, -1
      for node in om:iterate () do
        local p = node.properties
        local name = p ["node.name"]
        if name and not managed_sinks [name] and p ["node.virtual"] ~= "true"
           and (p ["media.class"] or ""):match ("Audio/Sink") then
          local prio = tonumber (p ["priority.session"] or "0") or 0
          if prio > best_prio then best_name, best_prio = name, prio end
        end
      end
      if best_name then
        log:info ("override default sink " .. selected .. " -> " .. best_name)
        event:set_data ("selected-node", best_name)
      end
    end
  end,
}:register ()

-- Pin game streams to the managed sink and Sunshine's recorder to the managed
-- monitor, authoritatively: a tagged stream is always re-pinned even if
-- stream-restore or the Flatpak portal pre-set another target (the host default
-- on a client disconnect/reconnect), which would otherwise strand game audio on
-- the host. Untagged streams are left to WirePlumber's native linking.
local lutils = require ("linking-utils")

local function find_linkable (om, node_name)
  return om:lookup {
    Constraint { "node.name", "=", node_name, type = "pw-global" },
  }
end

SimpleEventHook {
  name = "lts-audio/route-game-and-recorder",
  before = { "linking/find-best-target" },
  interests = {
    EventInterest {
      Constraint { "event.type", "=", "select-target" },
    },
  },
  execute = function (event)
    local source, om, si, si_props =
      lutils:unwrap_select_target_event (event)
    -- Authoritative for streams we manage: override any target that
    -- stream-restore or the Flatpak portal pre-set (e.g. the host default on a
    -- client disconnect/reconnect graph change). Streams we do not manage fall
    -- through the `if not want` guard below, so this never hijacks them.

    local marker = si_props ["lutristosunshine.stream"]
    local class = si_props ["media.class"] or ""
    local want
    if marker == "game" then
      want = audio_sink
    elseif (class:match ("Source") or class:match ("Record"))
       and (si_props ["application.name"] == "sunshine"
            or si_props ["media.name"] == "sunshine-record") then
      want = audio_sink .. ".monitor"
    end
    if not want then return end

    local t = find_linkable (om, want)
    if t then
      log:info ("pin " .. tostring (si_props ["node.name"]) .. " -> " .. want)
      event:set_data ("target", t)
    end
  end,
}:register ()

Log.info ("LTS audio policy registered")
"""


def _wireplumber_policy_script(state: Dict[str, Any]) -> str:
    audio_sink = _sunshine_audio_capture_target(state)
    managed = list(dict.fromkeys([audio_sink, *SUNSHINE_OWNED_SINK_NAMES]))
    table = "".join(f'  ["{name}"] = true,\n' for name in managed if name)
    return (
        _WIREPLUMBER_POLICY_SCRIPT_TEMPLATE
        .replace("__AUDIO_SINK__", audio_sink)
        .replace("__MANAGED_SINKS_TABLE__", table)
    )


def _wireplumber_policy_conf() -> str:
    return (
        "# LutrisToSunshine: register the host audio policy in WirePlumber.\n"
        "wireplumber.components = [\n"
        "  {\n"
        f"    name = {WIREPLUMBER_POLICY_SCRIPT_NAME}, type = script/lua,\n"
        "    provides = script.lts-audio-policy\n"
        "  }\n"
        "]\n"
        "wireplumber.profiles = {\n"
        "  main = {\n"
        "    script.lts-audio-policy = required\n"
        "  }\n"
        "}\n"
    )


def _remove_wireplumber_policy(state: Dict[str, Any]) -> None:
    for key in ("wireplumber_policy_script", "wireplumber_policy_conf"):
        try:
            Path(state["paths"][key]).unlink()
        except OSError:
            pass
    _reload_wireplumber()


def _reload_wireplumber() -> None:
    subprocess.run(
        ["systemctl", "--user", "reload-or-restart", "wireplumber"],
        text=True, capture_output=True, check=False,
    )


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
    sunshine_command = safe_string(state.get("sunshine_execstart")) or _svc.sunshine_binary() or "sunshine"
    sunshine_unit = _resolve_sunshine_unit(state)
    audio_sink = state["audio_sink"]
    _inject_heredoc = _INJECT_FLATPAK_AUDIO_ENV_HEREDOC.replace(
        "@FLAGS@", repr(sorted(FLATPAK_FLAG_OPTIONS))
    ).replace(
        "@VALUE_OPTS@", repr(sorted(FLATPAK_VALUE_OPTIONS))
    )
    _inject_heredoc_rendered = _rendered_inject_heredoc(_inject_heredoc, audio_sink)
    refresh_rate_sync_mode = _normalized_refresh_rate_sync_mode(state.get("refresh_rate_sync_mode"))
    custom_mode = _normalized_custom_display_mode(state.get("custom_display_mode"))
    custom_width = custom_mode["width"]
    custom_height = custom_mode["height"]
    custom_refresh = _format_refresh_rate_hz(custom_mode["refresh"]) or str(FALLBACK_FPS)
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
        if is_flatpak_command "$command_to_run"; then
            command_to_run="$(python3 - "$command_to_run" "$mangohud_config_value" <<'PY'
import shlex, sys
tokens = shlex.split(sys.argv[1])
env_val = sys.argv[2]
idx = 0
if len(tokens) >= 2 and tokens[:2] == ["flatpak-spawn", "--host"]:
    idx = 2
tokens.insert(idx + 2, f"--env=MANGOHUD_CONFIG={env_val}")
print(shlex.join(tokens))
PY
)"
        else
            launch_command+=("MANGOHUD_CONFIG=$mangohud_config_value")
        fi
    fi
"""
    gpu_card_path = safe_string(state.get("gpu_card_path"))
    gpu_render_path = safe_string(state.get("gpu_render_path"))
    gpu_detection_block = ""
    gpu_env_vars_block = ""
    gpu_launch_env_vars_block = ""
    if state.get("gpu_mode") == "manual" and gpu_card_path:
        gpu_detection_block = f"""
wlr_drm_devices_value=""
wlr_render_drm_device_value=""
if [[ -e "{gpu_card_path}" ]] && [[ -e "{gpu_render_path}" ]]; then
    wlr_drm_devices_value="{gpu_card_path}"
    wlr_render_drm_device_value="{gpu_render_path}"
else
    echo "lts-gpu: saved GPU paths not found, skipping WLR GPU vars" >&2
fi
"""
        gpu_env_vars_block = """
    if [[ -n $wlr_drm_devices_value ]]; then
        sway_cmd+=(
            "WLR_DRM_DEVICES=$wlr_drm_devices_value"
            "WLR_RENDER_DRM_DEVICE=$wlr_render_drm_device_value"
        )
    fi
"""
        gpu_launch_env_vars_block = """
    if [[ -n $wlr_drm_devices_value ]]; then
        launch_command+=(
            "WLR_DRM_DEVICES=$wlr_drm_devices_value"
            "WLR_RENDER_DRM_DEVICE=$wlr_render_drm_device_value"
        )
    fi
"""
    renderer_env_vars_block = ""
    if state.get("renderer_mode") == "vulkan":
        renderer_env_vars_block = """
    sway_cmd+=(
        "WLR_RENDERER=vulkan"
    )
"""
    return {
        Path(paths["sway_config"]): f"""# Managed by LutrisToSunshine display.
output HEADLESS-1 resolution {FALLBACK_WIDTH}x{FALLBACK_HEIGHT}@{FALLBACK_FPS}Hz
output HEADLESS-1 bg #111827 solid_color

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

{gpu_detection_block}

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

    sway_cmd=(
        /usr/bin/env -i
        "HOME=$home_value"
        "USER=$user_value"
        "LOGNAME=$logname_value"
        "SHELL=$shell_value"
        "PATH=$path_value"
        "LANG=$lang_value"
        "XDG_RUNTIME_DIR=$runtime_dir"
        "DBUS_SESSION_BUS_ADDRESS=$dbus_value"
        "XDG_SESSION_TYPE=wayland"
        "XDG_CURRENT_DESKTOP=sway"
        "XDG_SESSION_DESKTOP=sway"
        "SWAYSOCK={state['sway_socket']}"
        "WLR_BACKENDS=headless,libinput"
        "LIBSEAT_BACKEND=noop"
    )
{gpu_env_vars_block}{renderer_env_vars_block}
    sway_cmd+=(/usr/bin/sway --config "{paths['sway_config']}")
    "${{sway_cmd[@]}}" &
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

sunshine_cmd={shlex.quote(sunshine_command)}
if [[ "$sunshine_cmd" == *"flatpak run"* ]]; then
    sunshine_cmd="${{sunshine_cmd/flatpak run/flatpak run --env=WAYLAND_DISPLAY=$wayland_value --env=SWAYSOCK={state['sway_socket']} --filesystem=$runtime_dir/}}"
fi
exec /bin/sh -lc "$sunshine_cmd"
""",
        Path(paths["sunshine_wrapper_script"]): f"""#!/bin/bash
set -euo pipefail

state_path="{paths['state_path']}"
sunshine_conf="{paths['sunshine_conf']}"
audio_sink="{audio_sink}"
audio_create_script="{paths['audio_create_script']}"
audio_cleanup_script="{paths['audio_cleanup_script']}"
kwin_input_isolation_script="{paths['kwin_input_isolation_script']}"
sway_start_script="{paths['sway_start_script']}"
sunshine_start_script="{paths['sunshine_start_script']}"
display_file="{paths['wayland_display_file']}"
kwin_input_isolation_status_file="{paths['kwin_input_isolation_status_file']}"
sway_socket="{state['sway_socket']}"
runtime_dir="${{XDG_RUNTIME_DIR:-/run/user/$(id -u)}}"
dbus_value="${{DBUS_SESSION_BUS_ADDRESS:-unix:path=$runtime_dir/bus}}"
pulse_server_value="${{PULSE_SERVER:-}}"
pulse_clientconfig_value="${{PULSE_CLIENTCONFIG:-}}"
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
import sys
from pathlib import Path

state_path = Path(sys.argv[1])
conf_path = Path(sys.argv[2])
managed_sink = sys.argv[3]

try:
    state = json.loads(state_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    sys.exit(1)
if not isinstance(state, dict):
    state = {{}}

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


state_path.parent.mkdir(parents=True, exist_ok=True)
state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
PY
}}

restore_audio_state() {{
    python3 - "$state_path" "$sunshine_conf" <<'PY'
import json
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
if not isinstance(original, dict):
    original = {{}}

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

state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
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
    stop_child "$kwin_input_isolation_pid"
    stop_child "$sway_pid"
    rm -f "$kwin_input_isolation_status_file" "$display_file"
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
    )
    if [ -n "$pulse_server_value" ]; then
        command+=("PULSE_SERVER=$pulse_server_value")
    fi
    if [ -n "$pulse_clientconfig_value" ]; then
        command+=("PULSE_CLIENTCONFIG=$pulse_clientconfig_value")
    fi
    command+=("$@")
    "${{command[@]}}"
}}

if run_audio_command pactl list sinks short 2>/dev/null | grep -q "$sink_name"; then
    exit 0
fi

module_id="$(run_audio_command pactl load-module module-null-sink "sink_name=$sink_name" "sink_properties=device.description=$sink_name" 2>/dev/null || true)"
if [ -n "$module_id" ]; then
    echo "$module_id" > "$module_file"
fi
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
    )
    if [ -n "$pulse_server_value" ]; then
        command+=("PULSE_SERVER=$pulse_server_value")
    fi
    if [ -n "$pulse_clientconfig_value" ]; then
        command+=("PULSE_CLIENTCONFIG=$pulse_clientconfig_value")
    fi
    command+=("$@")
    "${{command[@]}}"
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
        Path(paths["kwin_input_isolation_script"]): _kwin_input_isolation_script(state),
        Path(paths["headless_prep_script"]): f"""#!/bin/bash
set -euo pipefail

encoded_command="${{1:-}}"

if [ -z "$encoded_command" ]; then
    echo "Missing encoded prep command." >&2
    exit 1
fi

if [ -f /.flatpak-info ]; then
    sunshine_env=()
    for var in SUNSHINE_CLIENT_FPS SUNSHINE_CLIENT_WIDTH SUNSHINE_CLIENT_HEIGHT SUNSHINE_CLIENT_HMAX SUNSHINE_CLIENT_VMAX; do
        if [ -n "${{!var:-}}" ]; then
            sunshine_env+=("--env=$var=${{!var}}")
        fi
    done
    exec flatpak-spawn --host "${{sunshine_env[@]}}" "{paths['headless_prep_script']}" "$@"
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
{_inject_heredoc_rendered}

run_headless_command() {{
    local command_to_run="$1"
    log_debug "running prep command: $command_to_run"
    
    {gpu_detection_block}

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
        "PULSE_PROP={AUDIO_STREAM_PULSE_PROP}"
        "PIPEWIRE_PROPS={AUDIO_STREAM_PIPEWIRE_PROPS}"
        "PULSE_SERVER=$pulse_server_value"
        "PULSE_CLIENTCONFIG=$pulse_clientconfig_value"
    )
    {gpu_launch_env_vars_block}
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
{_CLEAR_AUDIO_ACTIVATION_ENV}

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
decoded_command="$(inject_flatpak_audio_env "$decoded_command")"

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

if [ -f /.flatpak-info ]; then
    sunshine_env=()
    for var in SUNSHINE_CLIENT_FPS SUNSHINE_CLIENT_WIDTH SUNSHINE_CLIENT_HEIGHT SUNSHINE_CLIENT_HMAX SUNSHINE_CLIENT_VMAX; do
        if [ -n "${{!var:-}}" ]; then
            sunshine_env+=("--env=$var=${{!var}}")
        fi
    done
    exec flatpak-spawn --host "${{sunshine_env[@]}}" "{paths['launch_app_script']}" "$@"
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

{_inject_heredoc_rendered}


launch_headless_direct() {{
    local command_to_run="$1"
{mangohud_fps_limit_block.rstrip()}
    log_debug "launch command: $command_to_run"

    {gpu_detection_block}

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
        "PULSE_PROP={AUDIO_STREAM_PULSE_PROP}"
        "PIPEWIRE_PROPS={AUDIO_STREAM_PIPEWIRE_PROPS}"
        "PULSE_SERVER=$pulse_server_value"
        "PULSE_CLIENTCONFIG=$pulse_clientconfig_value"
    )
    {gpu_launch_env_vars_block}
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
{_CLEAR_AUDIO_ACTIVATION_ENV}

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
decoded_command="$(inject_flatpak_audio_env "$decoded_command")"

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
        Path(paths["get_gpu_addr"]): """#!/bin/bash
discrete_gpu=""
internal_gpu=""
for gpu in /sys/class/drm/card[0-9]; do
    # if no directory found continue
    [ -e "$gpu" ] || continue

    # get pci-id of card
    pci_addr=$(basename $(readlink -f "$gpu/device"))
    vendor_id=$(cat "$gpu/device/vendor")
    device_id=$(cat "$gpu/device/device")
    
    type="Unknown"

    # Logic for Intel (Vendor 0x8086)
    if [[ "$vendor_id" == "0x8086" ]]; then
        # Check for typical integrated adrress
        if [[ "$pci_addr" == "0000:00:02.0" ]]; then
            type="Integrated"
            internal_gpu=$pci_addr
        else
            type="Discrete"
            discrete_gpu=$pci_addr
        fi

    # Logic for AMD (Vendor 0x1002)
    elif [[ "$vendor_id" == "0x1002" ]]; then
        # Search on Northbridge in hwmon folder
        if ls "$gpu/device/hwmon"/hwmon*/in1_input >/dev/null 2>&1; then
            type="Integrated"
            internal_gpu=$pci_addr
        else
            type="Discrete"
            discrete_gpu=$pci_addr
        fi

    # Logic for NVIDIA (Vendor 0x10de)
    elif [[ "$vendor_id" == "0x10de" ]]; then
        # NVIDIA ist almost always descrete (except for old Tegra-Chips)
        type="Discrete"
        discrete_gpu=$pci_addr
    fi
done

echo $discrete_gpu-$internal_gpu
        """,
        Path(paths["resolve_stream_fps_script"]): f"""#!/bin/bash
set -euo pipefail

if [ -f /.flatpak-info ]; then
    sunshine_env=()
    for var in SUNSHINE_CLIENT_FPS SUNSHINE_CLIENT_WIDTH SUNSHINE_CLIENT_HEIGHT SUNSHINE_CLIENT_HMAX SUNSHINE_CLIENT_VMAX; do
        if [ -n "${{!var:-}}" ]; then
            sunshine_env+=("--env=$var=${{!var}}")
        fi
    done
    exec flatpak-spawn --host "${{sunshine_env[@]}}" "{paths['resolve_stream_fps_script']}" "$@"
fi

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

if [ -f /.flatpak-info ]; then
    sunshine_env=()
    for var in SUNSHINE_CLIENT_FPS SUNSHINE_CLIENT_WIDTH SUNSHINE_CLIENT_HEIGHT SUNSHINE_CLIENT_HMAX SUNSHINE_CLIENT_VMAX; do
        if [ -n "${{!var:-}}" ]; then
            sunshine_env+=("--env=$var=${{!var}}")
        fi
    done
    exec flatpak-spawn --host "${{sunshine_env[@]}}" "{paths['apply_exact_refresh_script']}" "$@"
fi

width="${{1}}"
height="${{2}}"
since_time="${{3:-}}"

exact_stream_fps="$("{paths['resolve_stream_fps_script']}" exact none "$since_time")"
if [ -z "$exact_stream_fps" ]; then
    exit 0
fi

if [ -f /.flatpak-info ]; then
    flatpak-spawn --host env SWAYSOCK="{state['sway_socket']}" swaymsg "output HEADLESS-1 mode ${{width}}x${{height}}@${{exact_stream_fps}}Hz" >/dev/null 2>&1 || true
else
    SWAYSOCK="{state['sway_socket']}" swaymsg "output HEADLESS-1 mode ${{width}}x${{height}}@${{exact_stream_fps}}Hz" >/dev/null 2>&1 || true
fi
""",
        Path(paths["set_resolution_script"]): f"""#!/bin/bash
set -euo pipefail

if [ ! -S "{state['sway_socket']}" ]; then
    exit 0
fi

swaymsg_cmd() {{
    if [ -f /.flatpak-info ]; then
        flatpak-spawn --host env SWAYSOCK="{state['sway_socket']}" swaymsg "$@"
    else
        SWAYSOCK="{state['sway_socket']}" swaymsg "$@"
    fi
}}

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
if [ "$mode" = "exact" ]; then
    # Exact mode: let apply_exact_refresh resolve the real FPS from
    # Sunshine's journal and do a single, correct mode switch.
    setsid "{paths['apply_exact_refresh_script']}" "${{target_width}}" "${{target_height}}" "$sync_since" >/dev/null 2>&1 &
else
    swaymsg_cmd "output HEADLESS-1 mode ${{target_width}}x${{target_height}}@${{target_fps}}Hz" >/dev/null 2>&1 || true
fi

""".replace("{custom_width}", str(custom_width)).replace("{custom_height}", str(custom_height)).replace("{custom_refresh}", custom_refresh),
        Path(paths["reset_resolution_script"]): f"""#!/bin/bash
set -euo pipefail

if [ ! -S "{state['sway_socket']}" ]; then
    exit 0
fi

if [ -f /.flatpak-info ]; then
    flatpak-spawn --host env SWAYSOCK="{state['sway_socket']}" swaymsg "output HEADLESS-1 mode {FALLBACK_WIDTH}x{FALLBACK_HEIGHT}@{FALLBACK_FPS}Hz" >/dev/null 2>&1 || true
else
    SWAYSOCK="{state['sway_socket']}" swaymsg "output HEADLESS-1 mode {FALLBACK_WIDTH}x{FALLBACK_HEIGHT}@{FALLBACK_FPS}Hz" >/dev/null 2>&1 || true
fi
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

    for path, content in _script_templates(state).items():
        _write_file(path, content, executable=path.suffix == ".sh")
    for path, content in _systemd_templates(state).items():
        _write_file(path, content)
    _write_file(Path(state["paths"]["wireplumber_policy_script"]), _wireplumber_policy_script(state))
    _write_file(Path(state["paths"]["wireplumber_policy_conf"]), _wireplumber_policy_conf())


def refresh_managed_files(state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if state is None:
        state = load_state()
    if not safe_string(state.get("sunshine_execstart")):
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


def _read_global_prep_cmd_list(sunshine_conf: Path) -> List[Dict[str, str]]:
    raw = _read_key_value(sunshine_conf, "global_prep_cmd")
    if not raw.get("present"):
        return []
    try:
        parsed = json.loads(raw.get("value", ""))
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [entry for entry in parsed if isinstance(entry, dict)]


_LEGACY_PREP_CMD_MARKERS = (
    "lutristosunshine-stream-audio-start",
    "lutristosunshine-stream-audio-stop",
)


def _strip_legacy_global_prep_cmd(state: Dict[str, Any]) -> None:
    """Remove the obsolete LTS stream-audio hooks from Sunshine's global_prep_cmd.

    The WirePlumber policy replaces the old do/undo scripts; strip any stale
    entries left from a previous install so Sunshine does not run dead commands.
    """
    sunshine_conf = Path(state["paths"]["sunshine_conf"])
    entries = _read_global_prep_cmd_list(sunshine_conf)
    kept = [
        entry for entry in entries
        if not any(
            marker in str(entry.get("do", "")) or marker in str(entry.get("undo", ""))
            for marker in _LEGACY_PREP_CMD_MARKERS
        )
    ]
    if len(kept) == len(entries):
        return
    if kept:
        _set_key_value(sunshine_conf, "global_prep_cmd", json.dumps(kept))
    else:
        _remove_key(sunshine_conf, "global_prep_cmd")

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


def _drain_stale_audio_activation_env() -> None:
    """Clear stale audio vars from the systemd/DBus activation environment.

    The Flatpak portal handoff imported ``PULSE_SINK``, ``PULSE_PROP``,
    and ``PIPEWIRE_PROPS`` into the global activation environment where
    they persist for the login session, permanently tagging host apps as
    game streams.  Sets the DBus and systemd values to empty; then
    ``systemctl --user unset-environment`` removes them from systemd.
    """
    try:
        if shutil.which("dbus-update-activation-environment"):
            subprocess.run(
                [
                    "dbus-update-activation-environment",
                    "--systemd",
                    "PULSE_SINK=",
                    "PULSE_PROP=",
                    "PIPEWIRE_PROPS=",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
    except OSError:
        pass
    try:
        subprocess.run(
            [
                "systemctl",
                "--user",
                "unset-environment",
                "PULSE_SINK",
                "PULSE_PROP",
                "PIPEWIRE_PROPS",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        pass


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
    _strip_legacy_global_prep_cmd(state)
    _reload_wireplumber()
    # Drain stale audio vars from the activation environment so host apps
    # never inherit the game marker, even without launching another flatpak.
    _drain_stale_audio_activation_env()
    _remember_sunshine_audio_sink(state)
    save_state(state)

    if not _install_udev_rule(state):
        _restore_sunshine_audio_sink(state)
        state["sunshine_audio_sink"] = None
        _remove_wireplumber_policy(state)
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
    _drain_stale_audio_activation_env()
    _remember_sunshine_audio_sink(state)
    save_state(state)
    sunshine_unit = _resolve_sunshine_unit(state)
    result = _svc.start_sunshine_unit(sunshine_unit)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        _svc.stop_sunshine_unit(sunshine_unit)
        _restore_sunshine_audio_sink(state)
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
    _restore_sunshine_audio_sink(state)
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
    _remove_wireplumber_policy(state)
    _strip_legacy_global_prep_cmd(state)

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
