"""Shared constants for the display package."""
import os
from pathlib import Path


PROFILE_NAME = "default"
CONFIG_ROOT = Path("~/.config/lutristosunshine").expanduser()
LEGACY_DISPLAY_DIRNAME = "virtual" + "display"
DISPLAY_ROOT = CONFIG_ROOT / "display"
LEGACY_DISPLAY_ROOT = CONFIG_ROOT / LEGACY_DISPLAY_DIRNAME
PROFILE_ROOT = DISPLAY_ROOT / PROFILE_NAME
BIN_ROOT = CONFIG_ROOT / "bin"
DEFAULT_SUNSHINE_UNIT = "app-dev.lizardbyte.app.Sunshine.service"
WIREPLUMBER_SCRIPTS_DIR = Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser() / "wireplumber" / "scripts"
WIREPLUMBER_CONF_DIR = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "wireplumber" / "wireplumber.conf.d"
WIREPLUMBER_POLICY_SCRIPT_NAME = "lts-audio-policy.lua"
WIREPLUMBER_POLICY_CONF_NAME = "54-lts-audio-policy.conf"
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
# Known Sunshine virtual devices, as (vendor, product, name). Used to render
# static sway rules and clean saved KDE device settings. Runtime sync also
# enables the identifiers Sunshine actually registers.
SUNSHINE_INPUT_DEVICES = (
    (0x1209, 0x0002, "libvirtualhid Keyboard"),
    (0x1209, 0x0003, "libvirtualhid Mouse"),
    (0x1209, 0x0003, "libvirtualhid Mouse (Absolute)"),
    (0xBEEF, 0xDEAD, "Keyboard_passthrough"),
    (0xBEEF, 0xDEAD, "Mouse_passthrough"),
    (0xBEEF, 0xDEAD, "Mouse_passthrough (absolute)"),
    (0xBEEF, 0xDEAD, "Touch_passthrough"),
    (0xBEEF, 0xDEAD, "Pen_passthrough"),
)
# Match by vendor id and by name, so Sunshine can add a device kind without
# adding another vendor id in a separate list.
SUNSHINE_INPUT_VENDOR_IDS = tuple(
    dict.fromkeys(vendor_id for vendor_id, _product_id, _name in SUNSHINE_INPUT_DEVICES)
)
# Name fragments that identify Sunshine's virtual devices regardless of the
# USB ids it happens to use. Compared against the lowercased name with
# underscores turned into spaces, so "Mouse_passthrough" matches both
# "Mouse_passthrough" and "Mouse passthrough".
SUNSHINE_INPUT_NAME_MARKERS = [
    "libvirtualhid",
    "Keyboard_passthrough",
    "Mouse_passthrough",
    "Touch_passthrough",
    "Pen_passthrough",
]
# How long the sync script waits for Sunshine to register its devices.
SUNSHINE_INPUT_SYNC_TIMEOUT_SECONDS = 20
# udev name patterns for the managed rule. udev matches case-sensitively, so
# these are written the way the device names actually appear ("libvirtualhid
# Mouse (Absolute)", "Keyboard_passthrough", ...).
SUNSHINE_INPUT_UDEV_NAME_PATTERNS = ("libvirtualhid*", "*_passthrough*")
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

_PCI_IDS_PATHS = [
    Path("/usr/share/hwdata/pci.ids"),
    Path("/usr/share/pci.ids"),
]
