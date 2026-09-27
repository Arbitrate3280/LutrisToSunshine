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
XDG_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser()
XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
XDG_RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
WIREPLUMBER_SCRIPTS_DIR = XDG_DATA_HOME / "wireplumber" / "scripts"
WIREPLUMBER_CONF_DIR = XDG_CONFIG_HOME / "wireplumber" / "wireplumber.conf.d"
WIREPLUMBER_POLICY_SCRIPT_NAME = "lts-audio-policy.lua"
WIREPLUMBER_POLICY_CONF_NAME = "54-lts-audio-policy.conf"
DISPLAY_STATE_PATH = DISPLAY_ROOT / "display.json"
LEGACY_STATE_PATH = LEGACY_DISPLAY_ROOT / f"{LEGACY_DISPLAY_DIRNAME}.json"
FLATPAK_PORTAL_UNIT = "flatpak-portal.service"
DISPLAY_SOCKET_PATH = f"/run/user/{os.getuid()}/lutristosunshine-display.sock"
WAYLAND_DISPLAY_PATH = PROFILE_ROOT / "wayland-display"
DEFAULT_CAPTURE_METHOD = "wlr"
CAPTURE_METHODS = ("wlr", "portal")
HEADLESS_OUTPUT_NAME = "HEADLESS-1"
# Private session bus + portal stack owned by the virtual display.  The socket
# lives in the runtime dir so the address is short enough for a unix socket and
# disappears with the user session.
PORTAL_BUS_SOCKET_NAME = "lutristosunshine-portal-bus"
PORTAL_BUS_ADDRESS_PATH = PROFILE_ROOT / "portal-bus-address"
PORTAL_BUS_PID_PATH = PROFILE_ROOT / "portal-bus.pid"
PORTAL_READY_PATH = PROFILE_ROOT / "portal-ready"
PORTAL_LOG_PATH = PROFILE_ROOT / "portal.log"
# Sunshine usually carries file capabilities (cap_sys_admin for KMS capture),
# which makes the kernel mark its processes AT_SECURE.  GLib then refuses
# DBUS_SESSION_BUS_ADDRESS and only looks for $XDG_RUNTIME_DIR/bus, so portal
# capture needs a private runtime dir whose `bus` entry is our portal bus.
PORTAL_RUNTIME_DIR = PROFILE_ROOT / "runtime"
PORTAL_CONFIG_HOME = PROFILE_ROOT / "portal-config"
PORTAL_ROUTING_CONF_NAME = "sway-portals.conf"
PORTAL_WLR_CONFIG_NAME = "config"
# Private portal directory: xdg-desktop-portal only ever sees the wlroots
# backend, so no other implementation (KDE, GTK, ...) can serve a request.
PORTAL_PORTALS_DIRNAME = "portals"
PORTAL_WLR_PORTAL_NAME = "wlr.portal"
PORTAL_DESKTOP_NAME = "org.freedesktop.portal.Desktop"
PORTAL_WLR_BACKEND_NAME = "org.freedesktop.impl.portal.desktop.wlr"
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
